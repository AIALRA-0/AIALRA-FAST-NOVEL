"""创建无需密钥即可体验五类联动视图的虚构演示书。"""

from __future__ import annotations

import json
import hashlib
import math
from dataclasses import dataclass
from pathlib import Path

from app.db import transaction
from app.importers import parse_book
from app.pipeline import add_evidence


DEMO_TEXT = """第一章 雾港来信

雾港的潮钟敲过六响，陆昭在旧灯塔收到林雪送来的守灯司密令。密令只写着一句：黑潮将在三日后越过归潮渡。林雪把一枚黑曜罗盘交给陆昭，罗盘的银针始终指向北方的百塔城。陆昭答应与林雪同行。

第二章 旧日回声

十年前，天镜台坍塌的那一夜，宋砚客从碎石下救出年幼的陆昭，并收他为徒。陆昭在雾港看见黑曜罗盘时想起这段往事，却记不起师父后来为何离开守灯司。

第三章 渡水北行

主线第二日，陆昭与林雪乘渡船离开雾港，沿白汐河逆流而上。傍晚，两人在归潮渡下船。守桥人说陆路已被蚀月会封锁，只能借古镜门前往百塔城。

第四章 镜门

林雪用守灯令开启归潮渡的古镜门。镜面像水一样裂开，两人穿过镜门，片刻后抵达百塔城南门。陆昭发现黑曜罗盘的银针改指城内的无灯塔。

第五章 无灯塔

主线第三日，陆昭与林雪登上无灯塔。蚀月会首领沈烬承认自己封锁归潮渡，并声称黑潮来自天镜台旧址。沈烬向陆昭拔剑，林雪亮出守灯令挡在两人之间。陆昭决定先查明师父宋砚客与黑潮的关系。
"""


def seed_demo(database_path: Path) -> None:
    """写入确定性的短篇和大部头演示数据。"""

    has_books = False
    with transaction(database_path) as connection:
        # 旧版演示库没有片段完成标记；启动时补齐，避免质量页误报正在分析。
        demo_segments = connection.execute(
            """
            SELECT s.book_id, s.id FROM segments s
            JOIN books b ON b.id = s.book_id
            WHERE b.author = '系统虚构样例'
            """
        ).fetchall()
        for segment in demo_segments:
            connection.execute(
                """
                INSERT OR IGNORE INTO segment_results(
                    book_id, segment_id, provider, model, prompt_version
                ) VALUES (?, ?, 'demo', 'built-in', 'demo-seed-v1')
                """,
                (segment["book_id"], segment["id"]),
            )
        has_books = connection.execute("SELECT 1 FROM books LIMIT 1").fetchone() is not None
    if has_books:
        _seed_generated_demos(database_path)
        _repair_generated_demos(database_path)
        _repair_short_demo_geography(database_path)
        return
    parsed = parse_book("雾川行记.txt", DEMO_TEXT.encode("utf-8"))
    with transaction(database_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO books(title, author, source_type, source_hash, original_filename, segment_count, character_count,
                language, corpus_kind, license_name, rights_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'zh-CN', 'synthetic', '系统虚构，不对应真实作品', 'synthetic')
            """,
            (
                "雾川行记 · 演示",
                "系统虚构样例",
                parsed.source_type,
                parsed.source_hash,
                parsed.original_filename,
                len(parsed.segments),
                parsed.character_count,
            ),
        )
        book_id = int(cursor.lastrowid)
        for segment in parsed.segments:
            connection.execute(
                """
                INSERT INTO segments(book_id, ordinal, chapter_title, anchor, text, char_start, char_end)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    book_id,
                    segment.ordinal,
                    segment.chapter_title,
                    segment.anchor,
                    segment.text,
                    segment.char_start,
                    segment.char_end,
                ),
            )
        segments = connection.execute("SELECT * FROM segments WHERE book_id = ? ORDER BY ordinal", (book_id,)).fetchall()
        for segment in segments:
            connection.execute(
                """
                INSERT INTO segment_results(book_id, segment_id, provider, model, prompt_version)
                VALUES (?, ?, 'demo', 'built-in', 'demo-seed-v1')
                """,
                (book_id, segment["id"]),
            )

        entity_specs = [
            ("person", "陆昭", "旧灯塔守望者，正追查师父失踪与黑潮的联系。", 1.0, 0, None, None, ["小昭"]),
            ("person", "林雪", "守灯司信使，熟悉古镜门，并与陆昭共同北行。", 0.9, 0, None, None, []),
            ("person", "宋砚客", "陆昭的师父，曾在天镜台救下他，后来离开守灯司。", 0.78, 1, None, None, ["师父"]),
            ("person", "沈烬", "蚀月会首领，封锁归潮渡并掌握黑潮线索。", 0.75, 4, None, None, []),
            ("faction", "守灯司", "维持镜门和沿河灯塔的组织。", 0.72, 0, None, None, []),
            ("faction", "蚀月会", "封锁北方道路并调查黑潮的势力。", 0.66, 2, None, None, []),
            ("place", "雾港", "故事起点，白汐河入海处的港城。", 0.9, 0, 16.0, 73.0, []),
            ("place", "天镜台", "十年前坍塌，如今被指为黑潮源头。", 0.72, 1, 27.0, 18.0, []),
            ("place", "归潮渡", "白汐河上游渡口，陆路与古镜门在此交汇。", 0.82, 2, 51.0, 62.0, []),
            ("place", "百塔城", "北方塔城，陆昭一行经镜门抵达。", 0.88, 0, 80.0, 34.0, []),
            ("place", "无灯塔", "百塔城内没有灯火的高塔，沈烬在此等候。", 0.7, 3, 88.0, 21.0, []),
        ]
        entity_ids: dict[str, int] = {}
        for kind, name, summary, importance, first_segment, x, y, aliases in entity_specs:
            cursor = connection.execute(
                """
                INSERT INTO entities(book_id, kind, name, summary, importance, first_segment, x, y, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'demo')
                """,
                (book_id, kind, name, summary, importance, first_segment, x, y),
            )
            entity_id = int(cursor.lastrowid)
            entity_ids[name] = entity_id
            for alias in aliases:
                connection.execute("INSERT INTO aliases(entity_id, alias) VALUES (?, ?)", (entity_id, alias))
            segment = next(item for item in segments if name in item["text"])
            add_evidence(connection, book_id, "entity", entity_id, segment["id"], segment["text"], name)

        relation_specs = [
            ("陆昭", "林雪", "同行", "二人接受密令后共同北行。", 0.98, 0, "陆昭答应与林雪同行"),
            ("陆昭", "宋砚客", "师徒", "宋砚客救下陆昭并收他为徒。", 0.99, 1, "宋砚客从碎石下救出年幼的陆昭，并收他为徒"),
            ("林雪", "守灯司", "效忠", "林雪持有守灯令并为守灯司传递密令。", 0.92, 0, "林雪送来的守灯司密令"),
            ("沈烬", "蚀月会", "首领", "沈烬是蚀月会首领。", 1.0, 4, "蚀月会首领沈烬"),
            ("沈烬", "陆昭", "敌对", "沈烬在无灯塔向陆昭拔剑。", 0.98, 4, "沈烬向陆昭拔剑"),
            ("宋砚客", "守灯司", "旧成员", "宋砚客后来离开守灯司，原因未知。", 0.82, 1, "师父后来为何离开守灯司"),
        ]
        for source, target, predicate, summary, confidence, ordinal, quote in relation_specs:
            cursor = connection.execute(
                """
                INSERT INTO claims(book_id, source_entity_id, target_entity_id, predicate, summary, confidence, first_segment, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'demo')
                """,
                (book_id, entity_ids[source], entity_ids[target], predicate, summary, confidence, ordinal),
            )
            add_evidence(connection, book_id, "claim", int(cursor.lastrowid), segments[ordinal]["id"], segments[ordinal]["text"], quote)

        event_specs = [
            ("天镜台旧日营救", "宋砚客在坍塌的天镜台救下年幼的陆昭并收徒。", 1, 0.0, "relative", "主线十年前", None, "", 0.99, "天镜台", 1, "十年前，天镜台坍塌的那一夜，宋砚客从碎石下救出年幼的陆昭，并收他为徒", [("陆昭", "被救者"), ("宋砚客", "救援者")]),
            ("雾港收到密令", "陆昭收到黑潮将越过归潮渡的警告，并决定与林雪同行。", 0, 1.0, "relative", "主线第一日清晨", None, "", 0.98, "雾港", 0, "黑潮将在三日后越过归潮渡", [("陆昭", "接受者"), ("林雪", "信使")]),
            ("沿白汐河北上", "陆昭与林雪乘渡船从雾港抵达归潮渡。", 2, 2.0, "relative", "主线第二日傍晚", None, "water", 0.99, "归潮渡", 2, "陆昭与林雪乘渡船离开雾港，沿白汐河逆流而上", [("陆昭", "同行者"), ("林雪", "同行者")]),
            ("穿越古镜门", "林雪开启古镜门，两人从归潮渡瞬间抵达百塔城。", 3, 3.0, "relative", "主线第二日夜间", None, "teleport", 0.99, "百塔城", 3, "两人穿过镜门，片刻后抵达百塔城南门", [("陆昭", "穿越者"), ("林雪", "开启者")]),
            ("无灯塔对峙", "沈烬承认封锁渡口并与陆昭对峙，黑潮源头指向天镜台旧址。", 4, 4.0, "relative", "主线第三日", None, "walk", 0.97, "无灯塔", 4, "沈烬承认自己封锁归潮渡，并声称黑潮来自天镜台旧址", [("陆昭", "调查者"), ("林雪", "保护者"), ("沈烬", "对峙者")]),
        ]
        for title, summary, narrative, story, kind, value, end, transport, confidence, location, ordinal, quote, participants in event_specs:
            cursor = connection.execute(
                """
                INSERT INTO events(
                    book_id, title, summary, narrative_order, story_order, temporal_kind, temporal_value,
                    temporal_end, confidence, location_entity_id, transport, first_segment, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'demo')
                """,
                (book_id, title, summary, narrative, story, kind, value, end, confidence, entity_ids[location], transport, ordinal),
            )
            event_id = int(cursor.lastrowid)
            add_evidence(connection, book_id, "event", event_id, segments[ordinal]["id"], segments[ordinal]["text"], quote)
            for name, role in participants:
                connection.execute(
                    "INSERT INTO event_participants(event_id, entity_id, role) VALUES (?, ?, ?)",
                    (event_id, entity_ids[name], role),
                )

        world_specs = [
            ("power", "镜门术", "守灯令可以开启古镜门，使旅者在两个固定门点之间瞬时移动。", 0.98, 3, "林雪用守灯令开启归潮渡的古镜门"),
            ("faction", "守灯司职责", "守灯司负责密令传递、灯塔和镜门维护。", 0.83, 0, "林雪送来的守灯司密令"),
            ("background", "黑潮危机", "黑潮预计在主线第四日越过归潮渡，疑似来自天镜台旧址。", 0.9, 4, "黑潮来自天镜台旧址"),
            ("geography", "白汐河交通", "雾港与归潮渡之间可走水路；归潮渡再经镜门连接百塔城。", 0.96, 2, "沿白汐河逆流而上"),
        ]
        for category, title, summary, confidence, ordinal, quote in world_specs:
            cursor = connection.execute(
                "INSERT INTO world_notes(book_id, category, title, summary, confidence, first_segment, created_by) VALUES (?, ?, ?, ?, ?, ?, 'demo')",
                (book_id, category, title, summary, confidence, ordinal),
            )
            add_evidence(connection, book_id, "world_note", int(cursor.lastrowid), segments[ordinal]["id"], segments[ordinal]["text"], quote)

        entry_specs = [
            ("item", "黑曜罗盘", "银针会指向当前关键目的地，其机制未知。", {"持有者": "陆昭", "当前指向": "无灯塔"}, 0.96, 0, "黑曜罗盘"),
            ("item", "守灯令", "守灯司凭证，也能开启归潮渡古镜门。", {"持有者": "林雪", "能力": "开启镜门"}, 0.99, 3, "守灯令"),
            ("term", "黑潮", "将在三日后越过归潮渡的威胁，来源可能是天镜台旧址。", {"状态": "逼近", "证据强度": "部分确认"}, 0.88, 0, "黑潮将在三日后越过归潮渡"),
            ("parameter", "主线倒计时", "从雾港密令算起，黑潮预计三日后抵达归潮渡。", {"剩余": "约一日", "基准": "主线第三日"}, 0.85, 0, "三日后"),
        ]
        for category, name, summary, attributes, confidence, ordinal, quote in entry_specs:
            cursor = connection.execute(
                """
                INSERT INTO entries(book_id, category, name, summary, attributes_json, confidence, first_segment, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'demo')
                """,
                (book_id, category, name, summary, json.dumps(attributes, ensure_ascii=False), confidence, ordinal),
            )
            add_evidence(connection, book_id, "entry", int(cursor.lastrowid), segments[ordinal]["id"], segments[ordinal]["text"], quote)
    _seed_generated_demos(database_path)
    _repair_generated_demos(database_path)
    _repair_short_demo_geography(database_path)


@dataclass(frozen=True)
class GeneratedDemoSpec:
    """一个可重复生成的联动演示书配置。"""

    title: str
    chapter_count: int
    people: tuple[str, ...]
    factions: tuple[str, ...]
    places: tuple[str, ...]
    theme: str
    nonlinear: bool = False


GENERATED_DEMOS = (
    GeneratedDemoSpec(
        title="霓虹追凶 · 都市群像演示",
        chapter_count=32,
        people=(
            "程野", "苏棠", "韩峥", "许知微", "梁墨", "周砺", "陈真", "顾蓝", "夏帆", "林策", "方屿",
            "吴桐", "白露", "江澄", "陆遥", "唐可", "魏沉", "秦川", "叶岚", "宋桥", "沈青", "赵衡",
        ),
        factions=("北城刑侦队", "星港传媒", "澜江市政厅", "灰塔基金", "第七码头联盟"),
        places=("北城分局", "白塔公寓", "第七码头", "旧报社", "星港大厦", "河西仓库", "南站", "市政档案馆", "海湾医院", "钟楼广场", "跨江大桥", "灰塔会所"),
        theme="一份失踪记者留下的加密录音，把刑警程野带入横跨媒体、港口和市政厅的旧案。",
    ),
    GeneratedDemoSpec(
        title="镜海回声 · 非线性叙事演示",
        chapter_count=36,
        people=(
            "闻舟", "栖月", "季衡", "阿璃", "洛原", "迟星", "元策", "祁霜", "沧溟", "南乔", "青檀", "玄照", "陆离",
            "白砚", "商羽", "容川", "谢临", "云织", "寒江", "苏幕", "顾弦", "段尘", "叶回", "宁夏", "楚灯", "江眠",
        ),
        factions=("镜海司", "归墟议会", "拾忆者", "潮生院", "无昼舰队", "白礁商盟"),
        places=("沉钟岛", "镜海港", "回声礁", "无昼城", "潮生院外庭", "归墟门", "白礁集市", "旧王庭", "星落湾", "记忆井", "断潮桥", "雾航站", "月背塔", "深蓝墓园"),
        theme="航海师闻舟不断进入被篡改的记忆，必须把十年前海难与眼前战争重新排成真实顺序。",
        nonlinear=True,
    ),
    GeneratedDemoSpec(
        title="长夜十二城 · 120章大型压力演示",
        chapter_count=120,
        people=(
            "顾行舟", "叶听澜", "谢无咎", "宁长风", "楚照", "苏晚晴", "陆天枢", "姜玄", "商九歌", "白砚秋", "温如昼", "沈孤鸿",
            "林渡", "洛青崖", "裴观星", "许惊尘", "周既明", "秦望舒", "唐雨眠", "韩千山", "宋知白", "江伏夜", "孟春生", "陈赤霄",
            "方见鹿", "祁连月", "魏南枝", "赵临渊", "夏侯烈", "上官虹", "慕容雪", "司空鹤", "独孤野", "东方澈", "欧阳霁", "公孙遥",
            "百里川", "南宫岚", "尉迟海", "令狐策", "段星河", "莫问心", "程照影", "柳听蝉", "薛归尘", "叶重楼", "顾沉霜", "纪长安",
            "石破军", "花无眠", "梅映雪", "兰若生", "竹青岑", "萧承影", "霍鸣沙", "罗浮生", "燕北辰", "楚怀瑾", "苏云起", "陆行简",
            "姜见微", "商别离", "白长庚", "温知夏",
        ),
        factions=("天衡院", "镇夜司", "赤霄军", "北斗商盟", "太虚宗", "幽都王庭", "镜湖书院", "流沙部", "十二城议会"),
        places=(
            "长安城", "青崖关", "镜湖", "流沙驿", "赤霄堡", "无昼谷", "太虚山", "白帝城", "寒江渡", "星落原", "幽都", "天衡院总坛",
            "云梦泽", "断剑台", "北斗港", "沉月井", "离火城", "玄冰宫", "归雁岭", "千机坊", "雷泽", "空桑城", "望海楼", "终夜门",
        ),
        theme="顾行舟携带能够记录谎言的天衡简，沿十二城追查永夜来源，各方势力在远征途中不断结盟与反目。",
        nonlinear=True,
    ),
)


def _seed_generated_demos(database_path: Path) -> None:
    """保证每一种规模的演示书只写入一次。"""

    for spec in GENERATED_DEMOS:
        with transaction(database_path) as connection:
            if connection.execute("SELECT 1 FROM books WHERE title = ?", (spec.title,)).fetchone() is not None:
                continue
            _insert_generated_demo(connection, spec)


def _insert_generated_demo(connection: object, spec: GeneratedDemoSpec) -> None:
    """生成带逐字证据、人物关系和连续路线的完整演示书。"""

    chapter_rows: list[tuple[str, str]] = []
    transports = ("road", "water", "walk", "flight", "teleport")
    for ordinal in range(spec.chapter_count):
        lead = spec.people[ordinal % len(spec.people)]
        partner = spec.people[(ordinal + 1) % len(spec.people)]
        faction = spec.factions[ordinal % len(spec.factions)]
        place = spec.places[ordinal % len(spec.places)]
        next_place = spec.places[(ordinal + 1) % len(spec.places)]
        transport = transports[ordinal % len(transports)]
        chapter_title = f"第{ordinal + 1:03d}章 {place}的线索"
        chapter_text = (
            f"{spec.theme} 主线第{ordinal + 1}日，{spec.people[0]}在{place}见到{lead}与{partner}。"
            f"{lead}与{partner}确认同盟关系，{lead}公开属于{faction}。"
            f"众人通过{transport}从{place}前往{next_place}，取得第{ordinal + 1}枚回声印。"
            f"回声印记录：第{ordinal + 1}道城门必须由同行者共同开启。"
        )
        chapter_rows.append((chapter_title, chapter_text))

    source_hash = hashlib.sha256((spec.title + "|demo-v3").encode("utf-8")).hexdigest()
    character_count = sum(len(title) + len(text) for title, text in chapter_rows)
    cursor = connection.execute(
        """
        INSERT INTO books(title, author, source_type, source_hash, original_filename, segment_count, character_count,
            language, corpus_kind, license_name, rights_status)
        VALUES (?, '系统大型联动样例', 'txt', ?, ?, ?, ?, 'zh-CN', 'synthetic', '系统虚构，不对应真实作品', 'synthetic')
        """,
        (spec.title, source_hash, f"{spec.title}.txt", spec.chapter_count, character_count),
    )
    book_id = int(cursor.lastrowid)
    offset = 0
    segment_ids: list[int] = []
    for ordinal, (chapter_title, chapter_text) in enumerate(chapter_rows):
        segment_id = int(connection.execute(
            """
            INSERT INTO segments(book_id, ordinal, chapter_title, anchor, text, char_start, char_end)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                book_id, ordinal, chapter_title, f"demo-v3-chapter-{ordinal + 1:04d}", chapter_text,
                offset, offset + len(chapter_text),
            ),
        ).lastrowid)
        segment_ids.append(segment_id)
        connection.execute(
            """
            INSERT INTO segment_results(book_id, segment_id, provider, model, prompt_version)
            VALUES (?, ?, 'demo', 'built-in', 'demo-seed-v3')
            """,
            (book_id, segment_id),
        )
        offset += len(chapter_text) + 2

    entity_ids: dict[str, int] = {}
    for index, name in enumerate(spec.people):
        first = index % spec.chapter_count
        partner = spec.people[(index + 1) % len(spec.people)]
        place = spec.places[first % len(spec.places)]
        faction = spec.factions[first % len(spec.factions)]
        entity_ids[name] = _insert_demo_entity(
            connection, book_id, "person", name,
            f"{name}最早在{place}与{partner}确认阶段同盟，并公开属于{faction}；这段归属决定了当时的合作对象。",
            1.0 if index == 0 else max(0.45, 0.88 - index * 0.004), first,
            None, None, segment_ids[first], chapter_rows[first][1], name,
        )
    for index, name in enumerate(spec.factions):
        first = index % spec.chapter_count
        representative = spec.people[first % len(spec.people)]
        entity_ids[name] = _insert_demo_entity(
            connection, book_id, "faction", name,
            f"{representative}最早公开表明属于{name}，这个归属把人物的阶段同盟与势力立场联系起来。",
            0.72, first, None, None, segment_ids[first], chapter_rows[first][1], name,
        )
    for index, name in enumerate(spec.places):
        first = index % spec.chapter_count
        x, y = _generated_place_coordinates(index, len(spec.places))
        entity_ids[name] = _insert_demo_entity(
            connection, book_id, "place", name,
            f"{name}最早出现在第{first + 1}日行程中，队伍从这里前往{spec.places[(index + 1) % len(spec.places)]}。",
            0.68, first, x, y, segment_ids[first], chapter_rows[first][1], name,
        )

    protagonist_id = entity_ids[spec.people[0]]
    connection.execute(
        "INSERT INTO book_settings(book_id, protagonist_entity_id, auto_protagonist) VALUES (?, ?, 0)",
        (book_id, protagonist_id),
    )
    for ordinal in range(spec.chapter_count):
        text = chapter_rows[ordinal][1]
        lead = spec.people[ordinal % len(spec.people)]
        partner = spec.people[(ordinal + 1) % len(spec.people)]
        faction = spec.factions[ordinal % len(spec.factions)]
        place = spec.places[ordinal % len(spec.places)]
        next_place = spec.places[(ordinal + 1) % len(spec.places)]
        transport = transports[ordinal % len(transports)]
        alliance_quote = f"{lead}与{partner}确认同盟关系"
        claim_id = int(connection.execute(
            """
            INSERT INTO claims(
                book_id, source_entity_id, target_entity_id, predicate, summary,
                confidence, first_segment, created_by
            ) VALUES (?, ?, ?, '阶段同盟', ?, 0.91, ?, 'demo')
            """,
            (book_id, entity_ids[lead], entity_ids[partner], f"第{ordinal + 1}日，二人确认阶段同盟。", ordinal),
        ).lastrowid)
        add_evidence(connection, book_id, "claim", claim_id, segment_ids[ordinal], text, alliance_quote)
        member_quote = f"{lead}公开属于{faction}"
        member_claim_id = int(connection.execute(
            """
            INSERT INTO claims(
                book_id, source_entity_id, target_entity_id, predicate, summary,
                confidence, first_segment, created_by
            ) VALUES (?, ?, ?, '成员', ?, 0.98, ?, 'demo')
            """,
            (book_id, entity_ids[lead], entity_ids[faction], member_quote, ordinal),
        ).lastrowid)
        add_evidence(connection, book_id, "claim", member_claim_id, segment_ids[ordinal], text, member_quote)

        narrative_order = ordinal
        story_order = float(ordinal)
        temporal_kind = "relative"
        if spec.nonlinear and ordinal % 7 == 3:
            story_order = float(max(0, ordinal - 12)) + 0.25
            temporal_kind = "flashback"
        event_quote = f"众人通过{transport}从{place}前往{next_place}"
        event_id = int(connection.execute(
            """
            INSERT INTO events(
                book_id, title, summary, narrative_order, story_order, temporal_kind,
                temporal_value, confidence, location_entity_id, transport, first_segment, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0.96, ?, ?, ?, 'demo')
            """,
            (
                book_id, f"第{ordinal + 1}日抵达{next_place}",
                f"{spec.people[0]}与{partner if lead == spec.people[0] else lead}沿主线从{place}移动到{next_place}并取得线索。",
                narrative_order, story_order, temporal_kind, f"主线第{ordinal + 1}日",
                entity_ids[next_place], transport, ordinal,
            ),
        ).lastrowid)
        add_evidence(connection, book_id, "event", event_id, segment_ids[ordinal], text, event_quote)
        participant_roles: list[tuple[str, str]] = []
        seen_participants: set[str] = set()
        for participant, role in ((spec.people[0], "主线人物"), (lead, "当章行动者"), (partner, "同行者")):
            if participant in seen_participants:
                continue
            seen_participants.add(participant)
            participant_roles.append((participant, role))
        for participant, role in participant_roles:
            connection.execute(
                "INSERT OR IGNORE INTO event_participants(event_id, entity_id, role) VALUES (?, ?, ?)",
                (event_id, entity_ids[participant], role),
            )

        if ordinal % 3 == 0:
            entry_quote = f"第{ordinal + 1}枚回声印"
            entry_id = int(connection.execute(
                """
                INSERT INTO entries(
                    book_id, category, name, summary, attributes_json,
                    confidence, first_segment, created_by
                ) VALUES (?, 'item', ?, ?, ?, 0.95, ?, 'demo')
                """,
                (
                    book_id, entry_quote, "记录一段已经核验的路线信息。",
                    json.dumps({"编号": ordinal + 1, "取得地点": next_place}, ensure_ascii=False), ordinal,
                ),
            ).lastrowid)
            add_evidence(connection, book_id, "entry", entry_id, segment_ids[ordinal], text, entry_quote)

    _insert_generated_world_notes(connection, spec, book_id, segment_ids, chapter_rows)
    _ensure_generated_demo_narrative_units(connection, spec, book_id)


def _generated_place_coordinates(index: int, total: int) -> tuple[float, float]:
    """把压力演示地点排成东西向折线路线，避免圆环伪装成地理地图。"""

    columns = max(4, math.ceil(math.sqrt(max(1, total) * 1.7)))
    rows = max(1, math.ceil(total / columns))
    row = index // columns
    column = index % columns
    if row % 2:
        column = columns - 1 - column
    x = 10.0 + column * (80.0 / max(1, columns - 1))
    y = 14.0 + row * (72.0 / max(1, rows - 1))
    y += ((index * 17) % 7 - 3) * 1.8
    return round(x, 2), round(max(8.0, min(92.0, y)), 2)


def _insert_generated_world_notes(
    connection: object,
    spec: GeneratedDemoSpec,
    book_id: int,
    segment_ids: list[int],
    chapter_rows: list[tuple[str, str]],
) -> None:
    """用少量有差异的说明替代按章节复制的世界卡片。"""

    first_text = chapter_rows[0][1]
    first_route = f"众人通过road从{spec.places[0]}前往{spec.places[1]}"
    first_member = f"{spec.people[0]}公开属于{spec.factions[0]}"
    first_rule = "第1道城门必须由同行者共同开启"
    notes = (
        ("background", f"{spec.title.split('·')[0].strip()}的核心危机", spec.theme, spec.theme),
        ("faction", "人物与势力的公开归属", f"人物会公开表明所属势力，归属关系会影响后续结盟与冲突。", first_member),
        ("geography", "主线地点的交通连接", f"主线从{spec.places[0]}出发，地点之间会通过陆路、水路、步行、飞行或穿越依次连接。", first_route),
        ("rule", "同行者共同开启城门", "回声印记载城门需要同行者共同开启，这条约束会影响队伍能否继续前进。", first_rule),
    )
    for category, title, summary, quote in notes:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO world_notes(
                book_id, category, title, summary, confidence, first_segment, created_by
            ) VALUES (?, ?, ?, ?, 0.93, 0, 'demo')
            """,
            (book_id, category, title, summary),
        )
        note_id = int(cursor.lastrowid) if cursor.lastrowid else int(connection.execute(
            "SELECT id FROM world_notes WHERE book_id = ? AND category = ? AND title = ? ORDER BY id LIMIT 1",
            (book_id, category, title),
        ).fetchone()["id"])
        add_evidence(connection, book_id, "world_note", note_id, segment_ids[0], first_text, quote)


def _ensure_generated_demo_narrative_units(connection: object, spec: GeneratedDemoSpec, book_id: int) -> None:
    """为非线性压力演示提供明确的故事单元；普通章节仍只属于阅读进度。"""

    if not spec.nonlinear:
        return
    existing = connection.execute(
        "SELECT id, world_id FROM narrative_units WHERE book_id = ? AND created_by = 'demo_partition' ORDER BY id LIMIT 1",
        (book_id,),
    ).fetchone()
    if existing is not None:
        # 旧版启动时可能已经为同一本演示书生成了一个低置信度 local_partition 世界；
        # 合并回那个世界，避免用户看到两个同名范围，且不会触碰人工确认的世界
        legacy_world = connection.execute(
            """
            SELECT world.id FROM story_worlds world
            WHERE world.book_id = ? AND world.created_by = 'local_partition'
              AND NOT EXISTS (
                SELECT 1 FROM narrative_units unit
                WHERE unit.world_id = world.id AND unit.created_by = 'human'
              )
            ORDER BY world.id LIMIT 1
            """,
            (book_id,),
        ).fetchone()
        if legacy_world is not None and int(legacy_world["id"]) != int(existing["world_id"]):
            duplicate_world_id = int(existing["world_id"])
            target_world_id = int(legacy_world["id"])
            connection.execute(
                "UPDATE narrative_units SET world_id = ? WHERE world_id = ? AND created_by = 'demo_partition'",
                (target_world_id, duplicate_world_id),
            )
            connection.execute("DELETE FROM story_worlds WHERE id = ?", (duplicate_world_id,))
        return
    legacy_world = connection.execute(
        "SELECT id FROM story_worlds WHERE book_id = ? AND created_by = 'local_partition' ORDER BY id LIMIT 1",
        (book_id,),
    ).fetchone()
    if legacy_world is not None:
        world_id = int(legacy_world["id"])
    else:
        world_cursor = connection.execute(
            """
            INSERT INTO story_worlds(book_id, name, status, confidence, evidence_json, created_by)
            VALUES (?, ?, 'suggested', 0.92, ?, 'demo_partition')
            """,
            (
                book_id,
                f"{spec.title.split('·')[0].strip()}演示世界",
                json.dumps(["压力演示明确提供非线性剧情单元；该分区只用于功能验收"], ensure_ascii=False),
            ),
        )
        world_id = int(world_cursor.lastrowid)
    total = max(1, int(spec.chapter_count))
    unit_count = min(4, total)
    for index in range(unit_count):
        start = (total * index) // unit_count
        end = (total * (index + 1)) // unit_count - 1
        representative = spec.places[start % len(spec.places)]
        next_place = spec.places[(start + 1) % len(spec.places)]
        title = f"第{index + 1}幕；{representative}—{next_place}"
        connection.execute(
            """
            INSERT INTO narrative_units(
                book_id, world_id, title, start_segment, end_segment, unit_kind,
                status, confidence, evidence_json, created_by
            ) VALUES (?, ?, ?, ?, ?, 'story', 'suggested', 0.9, ?, 'demo_partition')
            """,
            (
                book_id,
                world_id,
                title,
                start,
                end,
                json.dumps(["压力演示的独立幕边界", "幕间保留同一世界与主线人物"], ensure_ascii=False),
            ),
        )


def _repair_generated_demos(database_path: Path) -> None:
    """升级已有内置演示的地图坐标和重复世界卡片，不触碰用户导入书籍。"""

    for spec in GENERATED_DEMOS:
        with transaction(database_path) as connection:
            book = connection.execute(
                "SELECT id FROM books WHERE title = ? AND author = '系统大型联动样例'",
                (spec.title,),
            ).fetchone()
            if book is None:
                continue
            book_id = int(book["id"])
            for index, name in enumerate(spec.places):
                x, y = _generated_place_coordinates(index, len(spec.places))
                connection.execute(
                    "UPDATE entities SET x = ?, y = ? WHERE book_id = ? AND kind = 'place' AND name = ? AND created_by = 'demo'",
                    (x, y, book_id, name),
                )
            for index, name in enumerate(spec.people):
                first = index % spec.chapter_count
                summary = (
                    f"{name}最早在{spec.places[first % len(spec.places)]}与"
                    f"{spec.people[(index + 1) % len(spec.people)]}确认阶段同盟，并公开属于"
                    f"{spec.factions[first % len(spec.factions)]}；这段归属决定了当时的合作对象。"
                )
                connection.execute(
                    "UPDATE entities SET summary = ? WHERE book_id = ? AND kind = 'person' AND name = ? AND created_by = 'demo'",
                    (summary, book_id, name),
                )
            for index, name in enumerate(spec.factions):
                representative = spec.people[(index % spec.chapter_count) % len(spec.people)]
                summary = f"{representative}最早公开表明属于{name}，这个归属把人物的阶段同盟与势力立场联系起来。"
                connection.execute(
                    "UPDATE entities SET summary = ? WHERE book_id = ? AND kind = 'faction' AND name = ? AND created_by = 'demo'",
                    (summary, book_id, name),
                )
            for index, name in enumerate(spec.places):
                first = index % spec.chapter_count
                summary = f"{name}最早出现在第{first + 1}日行程中，队伍从这里前往{spec.places[(index + 1) % len(spec.places)]}。"
                connection.execute(
                    "UPDATE entities SET summary = ? WHERE book_id = ? AND kind = 'place' AND name = ? AND created_by = 'demo'",
                    (summary, book_id, name),
                )
            obsolete = connection.execute(
                "SELECT id FROM world_notes WHERE book_id = ? AND created_by = 'demo' AND title GLOB '第*道城门规则'",
                (book_id,),
            ).fetchall()
            for note in obsolete:
                connection.execute(
                    "DELETE FROM evidence WHERE target_type = 'world_note' AND target_id = ?",
                    (note["id"],),
                )
                connection.execute("DELETE FROM world_notes WHERE id = ?", (note["id"],))
            segments = connection.execute(
                "SELECT id, chapter_title, text FROM segments WHERE book_id = ? ORDER BY ordinal",
                (book_id,),
            ).fetchall()
            if segments:
                chapter_rows = [(str(item["chapter_title"]), str(item["text"])) for item in segments]
                segment_ids = [int(item["id"]) for item in segments]
                _insert_generated_world_notes(connection, spec, book_id, segment_ids, chapter_rows)
            _ensure_generated_demo_narrative_units(connection, spec, book_id)


def _repair_short_demo_geography(database_path: Path) -> None:
    """为旧版短篇演示补上原文明示的三条地点方位。"""

    with transaction(database_path) as connection:
        book = connection.execute(
            "SELECT id FROM books WHERE title = '雾川行记 · 演示' AND author = '系统虚构样例'",
        ).fetchone()
        if book is None:
            return
        book_id = int(book["id"])
        entities = connection.execute(
            "SELECT id, name FROM entities WHERE book_id = ? AND kind = 'place'",
            (book_id,),
        ).fetchall()
        entity_ids = {str(item["name"]): int(item["id"]) for item in entities}
        specs = (
            ("百塔城", "雾港", "north", "百塔城位于雾港以北。", 0, "罗盘的银针始终指向北方的百塔城"),
            ("归潮渡", "雾港", "upstream", "归潮渡位于雾港沿白汐河的上游。", 2, "陆昭与林雪乘渡船离开雾港，沿白汐河逆流而上。傍晚，两人在归潮渡下船"),
            ("无灯塔", "百塔城", "inside", "无灯塔位于百塔城内。", 3, "抵达百塔城南门。陆昭发现黑曜罗盘的银针改指城内的无灯塔"),
        )
        for source, target, position, summary, ordinal, quote in specs:
            if source not in entity_ids or target not in entity_ids:
                continue
            segment = connection.execute(
                "SELECT id, text FROM segments WHERE book_id = ? AND ordinal = ?",
                (book_id, ordinal),
            ).fetchone()
            if segment is None or quote not in segment["text"]:
                continue
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO place_relations(
                    book_id, source_entity_id, target_entity_id, relative_position,
                    summary, confidence, first_segment, created_by
                ) VALUES (?, ?, ?, ?, ?, 0.96, ?, 'demo')
                """,
                (book_id, entity_ids[source], entity_ids[target], position, summary, ordinal),
            )
            relation_id = int(cursor.lastrowid) if cursor.lastrowid else int(connection.execute(
                """
                SELECT id FROM place_relations WHERE book_id = ? AND source_entity_id = ?
                  AND target_entity_id = ? AND relative_position = ? ORDER BY id LIMIT 1
                """,
                (book_id, entity_ids[source], entity_ids[target], position),
            ).fetchone()["id"])
            add_evidence(connection, book_id, "place_relation", relation_id, int(segment["id"]), str(segment["text"]), quote)


def _insert_demo_entity(
    connection: object,
    book_id: int,
    kind: str,
    name: str,
    summary: str,
    importance: float,
    first_segment: int,
    x: float | None,
    y: float | None,
    segment_id: int,
    text: str,
    quote: str,
) -> int:
    """写入一个实体并立即绑定所在章节的逐字证据。"""

    entity_id = int(connection.execute(
        """
        INSERT INTO entities(
            book_id, kind, name, summary, importance, first_segment, x, y, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'demo')
        """,
        (book_id, kind, name, summary, importance, first_segment, x, y),
    ).lastrowid)
    add_evidence(connection, book_id, "entity", entity_id, segment_id, text, quote)
    return entity_id
