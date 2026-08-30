"""验证证据门槛和时间顺序数据不会被模型输出绕过。"""

from __future__ import annotations

from pathlib import Path

from app.db import connect, initialize, transaction
from app.consolidation import consolidate_book, merge_entities
from app.models import (
    EntityCandidate,
    EventCandidate,
    EventCausalReferenceCandidate,
    EventNarrativeFrameCandidate,
    ExtractionResult,
    JourneyLegCandidate,
    ParticipantCandidate,
    PlaceRelationCandidate,
    WorldNoteCandidate,
)
from app.pipeline import find_quote, persist_extraction


def create_book(path: Path) -> tuple[int, object]:
    """创建一个最小书籍与证据片段。"""

    initialize(path)
    with transaction(path) as connection:
        cursor = connection.execute(
            "INSERT INTO books(title, source_type, source_hash, original_filename, segment_count, character_count) VALUES ('测试', 'txt', 'hash', 'test.txt', 1, 12)"
        )
        book_id = int(cursor.lastrowid)
        cursor = connection.execute(
            "INSERT INTO segments(book_id, ordinal, chapter_title, anchor, text, char_start, char_end) VALUES (?, 0, '第一章', 'seg-0-test', '陆昭抵达雾港。', 0, 8)",
            (book_id,),
        )
        segment_id = int(cursor.lastrowid)
    connection = connect(path)
    segment = connection.execute("SELECT * FROM segments WHERE id = ?", (segment_id,)).fetchone()
    connection.close()
    return book_id, segment


def test_rejects_candidate_without_exact_quote(tmp_path: Path) -> None:
    """模型编造的引文不能生成实体。"""

    path = tmp_path / "test.db"
    book_id, segment = create_book(path)
    extraction = ExtractionResult(
        entities=[
            EntityCandidate(
                name="陆昭",
                kind="person",
                summary="旅者。",
                importance=0.8,
                evidence_quote="陆昭出生在雾港",
            )
        ]
    )
    with transaction(path) as connection:
        stats = persist_extraction(connection, book_id, segment, extraction)
    with connect(path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    assert stats.rejected_without_evidence == 1
    assert count == 0


def test_quote_alignment_ignores_only_layout_whitespace() -> None:
    """来源换行可以对齐，字词或标点变化仍会被拒绝。"""

    source = "內育仙胞\n，一日迸裂，產一石卵。"
    assert find_quote(source, "內育仙胞，一日迸裂，產一石卵。") == (0, len(source))
    assert find_quote(source, "内育仙胞，一日迸裂，产一石卵。") is None
    assert find_quote(source, "內育仙胞，一日迸裂，產一石卵！") is None


def test_persists_entity_event_and_evidence(tmp_path: Path) -> None:
    """逐字引文、故事顺序和叙事顺序应一起写入。"""

    path = tmp_path / "test.db"
    book_id, segment = create_book(path)
    extraction = ExtractionResult(
        entities=[
            EntityCandidate(
                name="陆昭",
                kind="person",
                summary="抵达雾港的旅者。",
                importance=0.9,
                evidence_quote="陆昭",
            ),
            EntityCandidate(
                name="雾港",
                kind="place",
                summary="陆昭抵达的港口。",
                importance=0.7,
                evidence_quote="雾港",
            ),
        ],
        events=[
            EventCandidate(
                title="抵达雾港",
                summary="陆昭抵达雾港。",
                narrative_order=0,
                story_order=-2.0,
                temporal_kind="relative",
                temporal_value="回忆中的两年前",
                location="雾港",
                confidence=0.95,
                evidence_quote="陆昭抵达雾港。",
            )
        ],
    )
    with transaction(path) as connection:
        stats = persist_extraction(connection, book_id, segment, extraction)
    with connect(path) as connection:
        event = connection.execute("SELECT * FROM events").fetchone()
        evidence_count = connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
    assert stats.accepted == 3
    # 全书故事顺序由可核验的前后约束计算，不能直接信任片段模型给出的任意序号。
    assert event["story_order"] == 0.0
    assert event["narrative_order"] == 0
    # 地点同时保留名称证据和事件场景证据，便于之后审查位置绑定。
    assert evidence_count == 4


def test_narrative_frame_requires_exact_evidence_and_keeps_explicit_causality(tmp_path: Path) -> None:
    """叙事承接字段必须有逐字证据，单独核验通过的因果边才可保存。"""

    path = tmp_path / "narrative-frame.db"
    initialize(path)
    text = "陆昭收到求救信。因为求救信写明雾港被围，陆昭立即赶往雾港，决定先救出港民。"
    with transaction(path) as connection:
        book_id = int(connection.execute(
            "INSERT INTO books(title, source_type, source_hash, original_filename, segment_count, character_count) VALUES ('因果', 'txt', 'narrative-frame', 'frame.txt', 1, ?)",
            (len(text),),
        ).lastrowid)
        segment_id = int(connection.execute(
            "INSERT INTO segments(book_id, ordinal, chapter_title, anchor, text, char_start, char_end) VALUES (?, 0, '第一章', 'frame-0', ?, 0, ?)",
            (book_id, text, len(text)),
        ).lastrowid)
        segment = connection.execute("SELECT * FROM segments WHERE id = ?", (segment_id,)).fetchone()
        extraction = ExtractionResult(events=[
            EventCandidate(
                title="收到求救信", summary="陆昭收到求救信。", narrative_order=0,
                temporal_kind="unknown", confidence=0.95, evidence_quote="陆昭收到求救信。",
            ),
            EventCandidate(
                title="赶往雾港", summary="陆昭因雾港被围而赶去救人。", narrative_order=1,
                temporal_kind="unknown", confidence=0.96,
                evidence_quote="因为求救信写明雾港被围，陆昭立即赶往雾港，决定先救出港民。",
                narrative_frame=EventNarrativeFrameCandidate(
                    cause="求救信写明雾港被围", goal="先救出港民", action="立即赶往雾港",
                    open_threads=["能否救出港民"],
                    evidence_quotes=["求救信写明雾港被围", "决定先救出港民"],
                    causal_references=[EventCausalReferenceCandidate(
                        target_event="收到求救信", relation="motivates", evidence_quote="因为求救信写明雾港被围",
                    )],
                ),
            ),
            EventCandidate(
                title="未经支持的推断", summary="陆昭收到求救信。", narrative_order=2,
                temporal_kind="unknown", confidence=0.5, evidence_quote="陆昭收到求救信。",
                narrative_frame=EventNarrativeFrameCandidate(
                    cause="陆昭想成为英雄", action="收到求救信",
                    evidence_quotes=["原文不存在的心理"],
                ),
            ),
        ])
        stats = persist_extraction(connection, book_id, segment, extraction)
        frames = connection.execute(
            "SELECT event.title, frame.cause, frame.goal, frame.action, frame.open_threads_json FROM event_narrative_frames frame JOIN events event ON event.id = frame.event_id ORDER BY event.narrative_order"
        ).fetchall()
        causal = connection.execute("SELECT relation, evidence_json FROM event_causal_links").fetchall()
    supported = next(row for row in frames if row["title"] == "赶往雾港")
    unsupported = next(row for row in frames if row["title"] == "未经支持的推断")
    assert supported["cause"] == "求救信写明雾港被围"
    assert supported["goal"] == "先救出港民"
    assert "能否救出港民" in supported["open_threads_json"]
    assert unsupported["cause"] == ""
    assert unsupported["action"] == "陆昭收到求救信。"
    assert causal[0]["relation"] == "motivates"
    assert stats.rejected_without_evidence >= 2


def test_alias_in_later_segment_reuses_existing_entity(tmp_path: Path) -> None:
    """后文使用已经登记的别名时不会创建第二个人物。"""

    path = tmp_path / "alias.db"
    book_id, segment = create_book(path)
    first = ExtractionResult(
        entities=[
            EntityCandidate(
                name="陆昭",
                kind="person",
                aliases=["阿昭"],
                summary="抵达雾港的旅者。",
                importance=0.9,
                evidence_quote="陆昭",
            )
        ]
    )
    second = ExtractionResult(
        entities=[
            EntityCandidate(
                name="阿昭",
                kind="person",
                aliases=[],
                summary="继续前行的旅者。",
                importance=0.9,
                evidence_quote="陆昭",
            )
        ]
    )
    with transaction(path) as connection:
        persist_extraction(connection, book_id, segment, first)
        persist_extraction(connection, book_id, segment, second)
    with connect(path) as connection:
        entities = connection.execute("SELECT * FROM entities WHERE book_id = ?", (book_id,)).fetchall()
    assert len(entities) == 1
    assert entities[0]["name"] == "陆昭"


def test_place_relation_requires_named_places_and_exact_evidence(tmp_path: Path) -> None:
    """地点方位只有在两个地点和逐字引文同时成立时才进入地图。"""

    path = tmp_path / "geography.db"
    initialize(path)
    text = "归潮渡位于雾港上游。"
    with transaction(path) as connection:
        book_id = int(connection.execute(
            "INSERT INTO books(title, source_type, source_hash, original_filename, segment_count, character_count) VALUES ('测试', 'txt', 'geo-hash', 'geo.txt', 1, ?)",
            (len(text),),
        ).lastrowid)
        segment_id = int(connection.execute(
            "INSERT INTO segments(book_id, ordinal, chapter_title, anchor, text, char_start, char_end) VALUES (?, 0, '第一章', 'seg-geo', ?, 0, ?)",
            (book_id, text, len(text)),
        ).lastrowid)
        segment = connection.execute("SELECT * FROM segments WHERE id = ?", (segment_id,)).fetchone()
        extraction = ExtractionResult(
            entities=[
                EntityCandidate(name="归潮渡", kind="place", summary="上游渡口。", importance=0.8, evidence_quote="归潮渡"),
                EntityCandidate(name="雾港", kind="place", summary="下游港口。", importance=0.8, evidence_quote="雾港"),
            ],
            place_relations=[
                PlaceRelationCandidate(
                    source="归潮渡", target="雾港", relative_position="upstream",
                    summary="归潮渡位于雾港上游。", confidence=0.99,
                    evidence_quote="归潮渡位于雾港上游",
                )
            ],
        )
        stats = persist_extraction(connection, book_id, segment, extraction)
    with connect(path) as connection:
        relation = connection.execute("SELECT * FROM place_relations").fetchone()
        evidence = connection.execute("SELECT * FROM evidence WHERE target_type = 'place_relation'").fetchone()
    assert stats.accepted == 3
    assert relation["relative_position"] == "upstream"
    assert evidence is not None


def test_referenced_places_are_created_before_events_relations_and_journeys(tmp_path: Path) -> None:
    """模型把地点只写在事件或行程字段时，落库层仍应保留完整地图结构。"""

    path = tmp_path / "referenced-places.db"
    initialize(path)
    text = "陆昭从雾港沿水路抵达归潮渡，归潮渡位于雾港上游。"
    with transaction(path) as connection:
        book_id = int(connection.execute(
            "INSERT INTO books(title, source_type, source_hash, original_filename, segment_count, character_count) VALUES ('地图', 'txt', 'map-ref', 'map.txt', 1, ?)",
            (len(text),),
        ).lastrowid)
        segment_id = int(connection.execute(
            "INSERT INTO segments(book_id, ordinal, chapter_title, anchor, text, char_start, char_end) VALUES (?, 0, '第一章', 'map-ref-0', ?, 0, ?)",
            (book_id, text, len(text)),
        ).lastrowid)
        segment = connection.execute("SELECT * FROM segments WHERE id = ?", (segment_id,)).fetchone()
        extraction = ExtractionResult(
            entities=[EntityCandidate(
                name="陆昭", kind="person", summary="沿水路旅行的人。", importance=0.9,
                evidence_quote="陆昭",
            )],
            events=[EventCandidate(
                title="抵达归潮渡", summary="陆昭沿水路抵达归潮渡。", narrative_order=0,
                temporal_kind="unknown", location="归潮渡", transport="water",
                participants=[ParticipantCandidate(name="陆昭", role="旅行者")],
                confidence=0.98, evidence_quote="陆昭从雾港沿水路抵达归潮渡",
            )],
            place_relations=[PlaceRelationCandidate(
                source="归潮渡", target="雾港", relative_position="upstream",
                summary="归潮渡位于雾港上游。", confidence=0.99,
                evidence_quote="归潮渡位于雾港上游",
            )],
            journey_legs=[JourneyLegCandidate(
                subject_names=["陆昭"], from_location="雾港", to_location="归潮渡",
                transport="water", summary="陆昭从雾港沿水路抵达归潮渡。",
                confidence=0.98, evidence_quote="陆昭从雾港沿水路抵达归潮渡",
            )],
        )
        persist_extraction(connection, book_id, segment, extraction)
        places = connection.execute(
            "SELECT id, name FROM entities WHERE book_id = ? AND kind = 'place' ORDER BY name",
            (book_id,),
        ).fetchall()
        event = connection.execute("SELECT location_entity_id FROM events WHERE book_id = ?", (book_id,)).fetchone()
        relations = connection.execute("SELECT COUNT(*) FROM place_relations WHERE book_id = ?", (book_id,)).fetchone()[0]
        legs = connection.execute("SELECT COUNT(*) FROM journey_legs WHERE book_id = ?", (book_id,)).fetchone()[0]
    assert {row["name"] for row in places} == {"雾港", "归潮渡"}
    assert event["location_entity_id"] in {row["id"] for row in places}
    assert relations == 1
    assert legs == 1


def test_generic_shared_title_does_not_create_merge_candidate(tmp_path: Path) -> None:
    """两个人都被称为龙王时不能仅凭通用头衔建议合并。"""

    path = tmp_path / "generic-alias.db"
    initialize(path)
    with transaction(path) as connection:
        book_id = int(connection.execute(
            "INSERT INTO books(title, source_type, source_hash, original_filename, segment_count, character_count) VALUES ('龙王测试', 'txt', 'alias-generic', 'alias.txt', 1, 4)"
        ).lastrowid)
        left_id = int(connection.execute(
            "INSERT INTO entities(book_id, kind, name, summary, importance, first_segment) VALUES (?, 'person', '东海龙王', '东海之主', 0.8, 0)",
            (book_id,),
        ).lastrowid)
        right_id = int(connection.execute(
            "INSERT INTO entities(book_id, kind, name, summary, importance, first_segment) VALUES (?, 'person', '泾河龙王', '泾河之主', 0.8, 0)",
            (book_id,),
        ).lastrowid)
        connection.executemany(
            "INSERT INTO aliases(entity_id, alias) VALUES (?, '龙王')",
            [(left_id,), (right_id,)],
        )
        consolidate_book(connection, book_id, 0)
        count = connection.execute(
            "SELECT COUNT(*) FROM entity_merge_candidates WHERE book_id = ?",
            (book_id,),
        ).fetchone()[0]
        generic_key_count = connection.execute(
            "SELECT COUNT(*) FROM entity_keys WHERE book_id = ? AND normalized_name = '龙王'",
            (book_id,),
        ).fetchone()[0]
    assert count == 0
    assert generic_key_count == 0


def test_world_notes_merge_same_topic_and_keep_all_evidence(tmp_path: Path) -> None:
    """同一世界设定换一种标题再次出现时应合成一张卡，并保留两章证据。"""

    path = tmp_path / "world-dedup.db"
    initialize(path)
    texts = [
        "金角说吃唐僧肉可延寿长生。",
        "红孩儿听闻唐僧肉能长生不老。",
    ]
    with transaction(path) as connection:
        book_id = int(connection.execute(
            "INSERT INTO books(title, source_type, source_hash, original_filename, segment_count, character_count) VALUES ('设定测试', 'txt', 'world-dedup', 'world.txt', 2, ?)",
            (sum(map(len, texts)),),
        ).lastrowid)
        segments = []
        for ordinal, text in enumerate(texts):
            segment_id = int(connection.execute(
                "INSERT INTO segments(book_id, ordinal, chapter_title, anchor, text, char_start, char_end) VALUES (?, ?, ?, ?, ?, 0, ?)",
                (book_id, ordinal, f"第{ordinal + 1}章", f"seg-{ordinal}", text, len(text)),
            ).lastrowid)
            segments.append(connection.execute("SELECT * FROM segments WHERE id = ?", (segment_id,)).fetchone())
        persist_extraction(
            connection,
            book_id,
            segments[0],
            ExtractionResult(world_notes=[WorldNoteCandidate(
                category="background",
                title="唐僧肉延生长寿的传说",
                summary="金角说吃唐僧肉可延寿长生。",
                confidence=0.9,
                evidence_quote=texts[0],
            )]),
        )
        persist_extraction(
            connection,
            book_id,
            segments[1],
            ExtractionResult(world_notes=[WorldNoteCandidate(
                category="background",
                title="唐僧肉长生不老的传说",
                summary="红孩儿听闻唐僧肉能长生不老。",
                confidence=0.9,
                evidence_quote=texts[1],
            )]),
        )
        consolidate_book(connection, book_id, 1)
        note_count = connection.execute(
            "SELECT COUNT(*) FROM world_notes WHERE book_id = ?",
            (book_id,),
        ).fetchone()[0]
        evidence_count = connection.execute(
            "SELECT COUNT(*) FROM evidence WHERE book_id = ? AND target_type = 'world_note'",
            (book_id,),
        ).fetchone()[0]
        summary = connection.execute(
            "SELECT summary FROM world_notes WHERE book_id = ?",
            (book_id,),
        ).fetchone()[0]
    assert note_count == 1
    assert evidence_count == 2
    assert "金角" in summary and "红孩儿" in summary


def test_world_note_merge_removes_repeated_sentences() -> None:
    """跨章节合并保留补充事实，但同一句不会在综合卡中出现两次。"""

    from app.consolidation import _combine_summaries

    repeated = "花果山顶仙石孕育石猴，石猴发现水帘洞后称王。"
    result = _combine_summaries(
        f"{repeated}悟空后来出海访道。",
        f"{repeated}学成归来后聚集群猴。",
    )
    assert result.count(repeated) == 1
    assert "出海访道" in result
    assert "聚集群猴" in result

    detailed = _combine_summaries(
        "他访道学成归来，剿灭混世魔王，从傲来国取兵器，聚集四万七千余口猴群，七十二洞妖王参拜为尊，形成强大势力。",
        "通背猿猴指点其访道，学成归来后剿灭混世魔王、从傲来国取兵器、得金箍棒与披挂，并封四老猴为健将，七十二洞妖王参拜，形成四万七千余口的强大势力。",
    )
    assert detailed.count("剿灭混世魔王") == 1
    assert "金箍棒与披挂" in detailed


def test_merging_places_moves_geography_relations(tmp_path: Path) -> None:
    """地点别名合并时要同步迁移地图方位，不能触发外键失败。"""

    path = tmp_path / "place-merge.db"
    book_id, segment = create_book(path)
    with transaction(path) as connection:
        keep_id = int(connection.execute(
            "INSERT INTO entities(book_id, kind, name, summary, importance, first_segment) VALUES (?, 'place', '三星洞', '修行地', 0.8, 0)",
            (book_id,),
        ).lastrowid)
        remove_id = int(connection.execute(
            "INSERT INTO entities(book_id, kind, name, summary, importance, first_segment) VALUES (?, 'place', '斜月三星洞', '祖师洞府', 0.9, 0)",
            (book_id,),
        ).lastrowid)
        target_id = int(connection.execute(
            "INSERT INTO entities(book_id, kind, name, summary, importance, first_segment) VALUES (?, 'place', '灵台方寸山', '山名', 0.9, 0)",
            (book_id,),
        ).lastrowid)
        relation_id = int(connection.execute(
            "INSERT INTO place_relations(book_id, source_entity_id, target_entity_id, relative_position, summary, confidence, first_segment) VALUES (?, ?, ?, 'inside', '洞府位于山中', 0.9, 0)",
            (book_id, remove_id, target_id),
        ).lastrowid)
        connection.execute(
            "INSERT INTO evidence(book_id, target_type, target_id, segment_id, quote, quote_start, quote_end) VALUES (?, 'place_relation', ?, ?, '雾港', 4, 6)",
            (book_id, relation_id, segment["id"]),
        )
        merge_entities(connection, book_id, keep_id, remove_id, "测试地点别名")
        relation = connection.execute("SELECT * FROM place_relations WHERE id = ?", (relation_id,)).fetchone()
        removed = connection.execute("SELECT id FROM entities WHERE id = ?", (remove_id,)).fetchone()
    assert removed is None
    assert relation["source_entity_id"] == keep_id
    assert relation["target_entity_id"] == target_id


def test_world_synthesis_hides_same_topic_raw_card(tmp_path: Path) -> None:
    """综合说明与原始卡标题不同但主题相同时，应建立归并依据而不是重复展示。"""

    path = tmp_path / "world-synthesis-link.db"
    book_id, _ = create_book(path)
    with transaction(path) as connection:
        raw_id = int(connection.execute(
            "INSERT INTO world_notes(book_id, category, title, summary, confidence, first_segment, created_by) VALUES (?, 'rule', '天上一日地上一年', '天上一日等于地上一年。', 0.9, 0, 'model')",
            (book_id,),
        ).lastrowid)
        synthesis_id = int(connection.execute(
            "INSERT INTO world_notes(book_id, category, title, summary, confidence, first_segment, created_by) VALUES (?, 'rule', '天庭与凡间的时间流速差异', '天庭一天对应凡间一年。', 0.9, 0, 'synthesis')",
            (book_id,),
        ).lastrowid)
        consolidate_book(connection, book_id, 0)
        link = connection.execute(
            "SELECT * FROM synthesis_basis WHERE world_note_id = ? AND basis_type = 'world_note' AND basis_id = ?",
            (synthesis_id, raw_id),
        ).fetchone()
    assert link is not None
