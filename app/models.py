"""模型输出和接口请求的严格数据结构。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """拒绝模型悄悄增加未知字段。"""

    model_config = ConfigDict(extra="forbid")


class EntityCandidate(StrictModel):
    """带原文引文的人物、地点、势力或其他实体候选。"""

    name: str = Field(min_length=1, max_length=120)
    kind: Literal["person", "place", "faction", "creature", "other"]
    aliases: list[str] = Field(default_factory=list, max_length=20)
    summary: str = Field(min_length=1, max_length=600)
    importance: float = Field(ge=0, le=1)
    evidence_quote: str = Field(min_length=1, max_length=800)


class RelationCandidate(StrictModel):
    """人物或势力之间的有向关系。"""

    source: str = Field(min_length=1, max_length=120)
    target: str = Field(min_length=1, max_length=120)
    predicate: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=600)
    confidence: float = Field(ge=0, le=1)
    evidence_quote: str = Field(min_length=1, max_length=800)


class PlaceRelationCandidate(StrictModel):
    """原文明示的地点方位或包含关系。"""

    source: str = Field(min_length=1, max_length=120)
    target: str = Field(min_length=1, max_length=120)
    relative_position: Literal[
        "north", "south", "east", "west",
        "northeast", "northwest", "southeast", "southwest",
        "inside", "contains", "near", "upstream", "downstream",
    ]
    summary: str = Field(min_length=1, max_length=600)
    confidence: float = Field(ge=0, le=1)
    evidence_quote: str = Field(min_length=1, max_length=800)


class ParticipantCandidate(StrictModel):
    """事件中的参与者和角色。"""

    name: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=80)


class EventCausalReferenceCandidate(StrictModel):
    """当前事件与既有事件之间、由本段原文直接支持的因果关系。"""

    target_event: str = Field(min_length=1, max_length=160)
    relation: Literal["causes", "enables", "motivates", "resolves", "contradicts"]
    evidence_quote: str = Field(min_length=1, max_length=800)


class EventNarrativeFrameCandidate(StrictModel):
    """为连贯摘要保留因果、状态和未闭合线索，不允许用模型常识补齐。"""

    cause: str = Field(default="", max_length=500)
    trigger: str = Field(default="", max_length=500)
    goal: str = Field(default="", max_length=500)
    action: str = Field(default="", max_length=800)
    outcome: str = Field(default="", max_length=800)
    state_changes: list[str] = Field(default_factory=list, max_length=12)
    open_threads: list[str] = Field(default_factory=list, max_length=12)
    resolved_threads: list[str] = Field(default_factory=list, max_length=12)
    evidence_quotes: list[str] = Field(default_factory=list, max_length=12)
    causal_references: list[EventCausalReferenceCandidate] = Field(default_factory=list, max_length=12)


class EventCandidate(StrictModel):
    """区分叙事顺序与故事时间的事件候选。"""

    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=800)
    narrative_order: int = Field(ge=0)
    story_order: float = Field(default=0, ge=-1000000, le=1000000)
    narrative_phase: Literal["main", "flashback", "dream", "prophecy", "parallel", "unknown"] = "main"
    temporal_kind: Literal["exact", "interval", "relative", "unknown"]
    temporal_value: str = Field(default="", max_length=200)
    temporal_start: str | None = Field(default=None, max_length=80)
    temporal_end: str | None = Field(default=None, max_length=80)
    location: str | None = Field(default=None, max_length=120)
    transport: Literal["", "walk", "road", "water", "flight", "teleport", "other"] = ""
    participants: list[ParticipantCandidate] = Field(default_factory=list, max_length=30)
    reference_event: str | None = Field(default=None, max_length=160)
    relation_to_reference: Literal["before", "after", "during", "same", "unknown"] = "unknown"
    narrative_frame: EventNarrativeFrameCandidate = Field(default_factory=EventNarrativeFrameCandidate)
    confidence: float = Field(ge=0, le=1)
    evidence_quote: str = Field(min_length=1, max_length=800)


class JourneyLegCandidate(StrictModel):
    """原文明示的人物或队伍移动，独立于一般事件保存。"""

    subject_names: list[str] = Field(default_factory=list, min_length=1, max_length=20)
    from_location: str | None = Field(default=None, max_length=120)
    to_location: str | None = Field(default=None, max_length=120)
    via_locations: list[str] = Field(default_factory=list, max_length=12)
    transport: Literal["", "walk", "road", "water", "flight", "teleport", "other"] = ""
    summary: str = Field(min_length=1, max_length=800)
    confidence: float = Field(ge=0, le=1)
    evidence_quote: str = Field(min_length=1, max_length=800)


class WorldNoteCandidate(StrictModel):
    """力量、势力、背景、规则或范围设定。"""

    category: Literal["power", "faction", "background", "rule", "geography", "culture", "other"]
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=800)
    confidence: float = Field(ge=0, le=1)
    evidence_quote: str = Field(min_length=1, max_length=800)


class EntryCandidate(StrictModel):
    """可批量检索的物品、技能、属性或参数条目。"""

    category: Literal["item", "skill", "attribute", "parameter", "term", "other"]
    name: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=800)
    attributes: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0, le=1)
    evidence_quote: str = Field(min_length=1, max_length=800)


class ExtractionResult(StrictModel):
    """单个原文片段的完整抽取结果。"""

    entities: list[EntityCandidate] = Field(default_factory=list, max_length=80)
    relations: list[RelationCandidate] = Field(default_factory=list, max_length=80)
    place_relations: list[PlaceRelationCandidate] = Field(default_factory=list, max_length=40)
    events: list[EventCandidate] = Field(default_factory=list, max_length=80)
    journey_legs: list[JourneyLegCandidate] = Field(default_factory=list, max_length=60)
    world_notes: list[WorldNoteCandidate] = Field(default_factory=list, max_length=60)
    entries: list[EntryCandidate] = Field(default_factory=list, max_length=100)


class AnalyzeRequest(BaseModel):
    """受控分析请求。"""

    provider: Literal["mock", "deepseek", "moonshot", "codex_luna", "auto"] = "mock"
    max_segments: int = Field(default=3, ge=1, le=200)
    start_segment: int = Field(default=0, ge=0)


class ClaimPatch(BaseModel):
    """人工审核关系事实。"""

    status: Literal["unreviewed", "accepted", "rejected", "corrected"]
    summary: str | None = Field(default=None, min_length=1, max_length=800)
    reason: str = Field(default="", max_length=500)


class AnalysisJobRequest(BaseModel):
    """创建可恢复的整本书分析任务。"""

    provider: Literal["mock", "deepseek", "moonshot", "codex_luna", "auto"] = "mock"
    start_segment: int = Field(default=0, ge=0)
    end_segment: int | None = Field(default=None, ge=0)
    max_retries: int = Field(default=3, ge=1, le=8)
    reanalyze: bool = False
    max_cost_usd: float = Field(default=0.5, ge=0, le=1000)
    max_input_tokens: int = Field(default=500_000, ge=1_000, le=500_000_000)
    max_output_tokens: int = Field(default=120_000, ge=1_000, le=100_000_000)
    budget_mode: Literal["adaptive", "manual"] = "adaptive"
    review_mode: Literal["local", "full"] = "local"


class AnalysisJobAction(BaseModel):
    """暂停、继续或取消后台任务。"""

    action: Literal["pause", "resume", "cancel", "retry"]


class AnalysisBudgetPatch(BaseModel):
    """调整单个任务的预算策略。"""

    max_cost_usd: float = Field(ge=0, le=1000)
    max_input_tokens: int = Field(ge=1_000, le=500_000_000)
    max_output_tokens: int = Field(ge=1_000, le=100_000_000)
    budget_mode: Literal["adaptive", "manual"] = "adaptive"


class EntityMergeRequest(BaseModel):
    """把重复人物、地点或势力合并为一个规范实体。"""

    keep_entity_id: int = Field(gt=0)
    remove_entity_id: int = Field(gt=0)
    reason: str = Field(default="人工确认同一实体", max_length=500)


class BookSettingsPatch(BaseModel):
    """设置主角，主角路线会以此为准。"""

    protagonist_entity_id: int | None = Field(default=None, gt=0)
    auto_protagonist: bool = True


class LibraryFolderRequest(BaseModel):
    """创建或重命名书库文件夹。"""

    name: str = Field(min_length=1, max_length=120)
    parent_id: int | None = Field(default=None, gt=0)


class BookPatch(BaseModel):
    """修改书名、作者或所在文件夹。"""

    title: str | None = Field(default=None, min_length=1, max_length=240)
    author: str | None = Field(default=None, max_length=240)
    folder_id: int | None = Field(default=None, gt=0)
    move_to_root: bool = False


class BookUpdateResolution(BaseModel):
    """处理增量更新中发现的旧章节变化。"""

    action: Literal["keep_current", "import_as_new", "auto"]


class RecordPatch(BaseModel):
    """修改一个结构记录，并保留修改前后的值。"""

    field_name: Literal["name", "title", "summary", "temporal_value", "category", "status", "attributes"]
    new_value: str = Field(min_length=1, max_length=20_000)
    reason: str = Field(default="人工修正", max_length=500)


class WorldNoteCreate(BaseModel):
    """人工创建一条可继续编辑和补证据的世界信息。"""

    category: Literal["power", "faction", "background", "rule", "geography", "culture", "other"]
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=5_000)


class ConceptCreate(BaseModel):
    """创建每本书独立的知识概念、分类或文件夹。"""

    category: str = Field(min_length=1, max_length=80)
    preferred_label: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=5_000)
    aliases: list[str] = Field(default_factory=list, max_length=40)
    parent_concept_id: int | None = Field(default=None, gt=0)
    scheme: Literal["book", "custom"] = "custom"


class ConceptPatch(BaseModel):
    """修改概念名称、说明、别名、分类或归档状态。"""

    category: str | None = Field(default=None, min_length=1, max_length=80)
    preferred_label: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=5_000)
    aliases: list[str] | None = Field(default=None, max_length=40)
    status: Literal["active", "archived", "needs_classification"] | None = None
    parent_concept_id: int | None = Field(default=None, gt=0)
    move_to_root: bool = False


class KnowledgeClaimCreate(BaseModel):
    """在概念下建立一条带原文证据或明确外部来源的原子事实。"""

    concept_id: int = Field(gt=0)
    predicate: str = Field(min_length=1, max_length=120)
    value: Any
    source_kind: Literal["original_text", "external_fact", "human_note"] = "human_note"
    confidence: float = Field(default=1.0, ge=0, le=1)
    segment_id: int | None = Field(default=None, gt=0)
    evidence_quote: str = Field(default="", max_length=800)
    qualifiers: dict[str, Any] = Field(default_factory=dict)


class KnowledgeClaimPatch(BaseModel):
    """修改原子事实状态、值、限定条件或置信度。"""

    value: Any | None = None
    status: Literal["accepted", "parallel", "deprecated", "needs_resolution"] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    qualifiers: dict[str, Any] | None = None


class ConnectivityReviewPatch(BaseModel):
    """人工解决自动复审仍无法裁定的孤立节点。"""

    status: Literal["confirmed_isolated", "ambiguous"]
    reason: str = Field(min_length=2, max_length=1_000)


class ConnectivityLinkCreate(BaseModel):
    """用逐字原文证据为待复审节点人工补建关系。"""

    target_entity_id: int = Field(gt=0)
    predicate: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=2, max_length=600)
    segment_id: int = Field(gt=0)
    evidence_quote: str = Field(min_length=1, max_length=800)


class EventLocationReviewPatch(BaseModel):
    """用同一本书的地点和逐字原文解决剧情位置冲突。"""

    location_entity_id: int = Field(gt=0)
    segment_id: int = Field(gt=0)
    evidence_quote: str = Field(min_length=1, max_length=800)


class MergeCandidatePatch(BaseModel):
    """拒绝误报的实体合并建议。"""

    status: Literal["rejected"]


class ContradictionPatch(BaseModel):
    """人工裁决两条已保存事实之间的冲突。"""

    action: Literal["contextual", "false_positive", "quarantine"]
    reason: str = Field(min_length=2, max_length=1_000)


class TimeConflictPatch(BaseModel):
    """人工裁决会破坏编年顺序的时间约束。"""

    action: Literal["reject", "reverse", "quarantine"]
    reason: str = Field(min_length=2, max_length=1_000)


class BenchmarkCaseCreate(BaseModel):
    """人工登记一条可回到原文章节的准确率金标准。"""

    case_type: Literal[
        "identity_same", "identity_distinct", "event_present", "event_before",
        "main_subject", "journey_start", "segment_accounting", "fact_evidence",
        "quote_integrity",
    ]
    subject: str = Field(min_length=2, max_length=240)
    expected: dict[str, Any] = Field(default_factory=dict)
    source_segment: int = Field(ge=0)
    note: str = Field(min_length=2, max_length=1_000)
    critical: bool = True
    suite_name: str = Field(default="book-gold", min_length=2, max_length=120)
    origin: Literal["manual", "user_correction", "imported", "historical_feedback"] = "manual"
    holdout: bool = False
    confirmed_by_user: bool = True
    failure_category: str = Field(default="", max_length=120)


class BenchmarkCasePatch(BaseModel):
    """人工修改已登记金标准，保存后会立即重新计算本地结果。"""

    case_type: Literal[
        "identity_same", "identity_distinct", "event_present", "event_before",
        "main_subject", "journey_start", "segment_accounting", "fact_evidence",
        "quote_integrity",
    ] | None = None
    subject: str | None = Field(default=None, min_length=2, max_length=240)
    expected: dict[str, Any] | None = None
    source_segment: int | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, min_length=2, max_length=1_000)
    critical: bool | None = None
    suite_name: str | None = Field(default=None, min_length=2, max_length=120)
    origin: Literal["manual", "user_correction", "imported", "historical_feedback"] | None = None
    holdout: bool | None = None
    confirmed_by_user: bool | None = None
    failure_category: str | None = Field(default=None, max_length=120)


class BenchmarkCandidateResolve(BaseModel):
    """人工确认或拒绝由已有原文证据整理出的金标准候选。"""

    action: Literal["accept", "reject"]
    holdout: bool = False
    critical: bool | None = None
    note: str = Field(default="", max_length=1_000)


class CollaborationItemCreate(BaseModel):
    """把用户反馈转换成可验收、可追踪的协作事项。"""

    original_text: str = Field(min_length=2, max_length=4_000)
    interpreted_goal: str = Field(min_length=2, max_length=4_000)
    acceptance: list[str] = Field(min_length=1, max_length=20)
    impact: list[str] = Field(default_factory=list, max_length=20)
    estimated_cost_change_percent: float = Field(default=0, ge=-100, le=10_000)
    requires_confirmation: bool = False


class CollaborationItemPatch(BaseModel):
    """推进协作事项并保存验收证据或对应回归案例。"""

    status: Literal[
        "interpreted", "confirmed", "implementing", "validating", "released", "rejected"
    ] | None = None
    interpreted_goal: str | None = Field(default=None, min_length=2, max_length=4_000)
    acceptance: list[str] | None = Field(default=None, min_length=1, max_length=20)
    impact: list[str] | None = Field(default=None, max_length=20)
    evidence: list[str] | None = Field(default=None, max_length=40)
    regression_case_id: int | None = Field(default=None, gt=0)


class DomainRuleCreate(BaseModel):
    """用户用陈述句补充一条阅读方法，不补写小说事实。"""

    task_key: Literal[
        "all", "extraction", "global_review", "record_regeneration", "connectivity_audit"
    ] = "all"
    statement: str = Field(min_length=4, max_length=2_000)
    rationale: str = Field(default="", max_length=2_000)
    examples: list[str] = Field(default_factory=list, max_length=20)
    priority: int = Field(default=100, ge=1, le=1_000)
    active: bool = True


class DomainRulePatch(BaseModel):
    """修改、停用或重新排序现有阅读规则。"""

    task_key: Literal[
        "all", "extraction", "global_review", "record_regeneration", "connectivity_audit"
    ] | None = None
    statement: str | None = Field(default=None, min_length=4, max_length=2_000)
    rationale: str | None = Field(default=None, max_length=2_000)
    examples: list[str] | None = Field(default=None, max_length=20)
    priority: int | None = Field(default=None, ge=1, le=1_000)
    active: bool | None = None


class ExternalFactCreate(BaseModel):
    """登记一条带来源的作品外资料，它不会成为原文证据。"""

    statement: str = Field(min_length=4, max_length=2_000)
    source_label: str = Field(min_length=2, max_length=300)
    source_url: str = Field(default="", max_length=1_000)
    active: bool = True


class ExternalFactPatch(BaseModel):
    """修改或停用作品外资料。"""

    statement: str | None = Field(default=None, min_length=4, max_length=2_000)
    source_label: str | None = Field(default=None, min_length=2, max_length=300)
    source_url: str | None = Field(default=None, max_length=1_000)
    active: bool | None = None


class PromptDraftCreate(BaseModel):
    """从当前正式版本创建可比较、可试跑的提示词草稿。"""

    core_text: str | None = Field(default=None, min_length=20, max_length=80_000)
    task_text: str = Field(default="", max_length=40_000)
    change_note: str = Field(min_length=2, max_length=1_000)


class PromptTrialRequest(BaseModel):
    """使用一本书的单个片段试跑草稿，避免直接重跑整本书。"""

    book_id: int = Field(gt=0)
    segment_id: int = Field(gt=0)
    provider: Literal["mock", "deepseek", "moonshot", "codex_luna", "auto"] = "mock"


class ModelRaceRequest(BaseModel):
    """在当前金标准上比较模型资格，不默认发起整本书调用。"""

    providers: list[Literal["mock", "deepseek", "moonshot", "codex_luna"]] = Field(
        default_factory=lambda: ["codex_luna", "deepseek", "moonshot"], min_length=1, max_length=4
    )
    run_live_canary: bool = False
    segment_id: int | None = Field(default=None, gt=0)


class ModelRoutePatch(BaseModel):
    """调整自动路由开关和优先级；模型资格只能由评估门禁产生。"""

    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=1, le=10_000)
    reset_circuit: bool = False


class GlobalSynthesisCandidate(StrictModel):
    """由多条已证实事实整理出的世界说明。"""

    category: Literal["power", "faction", "background", "rule", "geography", "culture", "other"]
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=1_200)
    basis_keys: list[str] = Field(min_length=1, max_length=40)
    confidence: float = Field(ge=0, le=1)


class GlobalMergeSuggestion(StrictModel):
    """跨章节疑似同一实体建议，最终由用户确认。"""

    kind: Literal["person", "place", "faction", "creature", "other"]
    left_name: str = Field(min_length=1, max_length=120)
    right_name: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=400)
    confidence: float = Field(ge=0, le=1)
    basis_keys: list[str] = Field(default_factory=list, max_length=30)
    counterevidence: list[str] = Field(default_factory=list, max_length=20)


class GlobalOrderSuggestion(StrictModel):
    """两件已知事件之间的明确故事时间顺序。"""

    earlier_event_title: str = Field(min_length=1, max_length=160)
    later_event_title: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=400)
    confidence: float = Field(ge=0, le=1)


class GlobalContradictionCandidate(StrictModel):
    """两条已证实记录之间可能存在的冲突。"""

    left_key: str = Field(min_length=2, max_length=40)
    right_key: str = Field(min_length=2, max_length=40)
    summary: str = Field(min_length=1, max_length=600)
    confidence: float = Field(ge=0, le=1)


class GlobalReviewResult(StrictModel):
    """全书分层整理的严格输出。"""

    syntheses: list[GlobalSynthesisCandidate] = Field(default_factory=list, max_length=40)
    merge_suggestions: list[GlobalMergeSuggestion] = Field(default_factory=list, max_length=40)
    order_suggestions: list[GlobalOrderSuggestion] = Field(default_factory=list, max_length=60)
    contradictions: list[GlobalContradictionCandidate] = Field(default_factory=list, max_length=40)
    protagonist_name: str | None = Field(default=None, max_length=120)


class ConnectivityRelationCandidate(StrictModel):
    """专项复审找到的一条遗漏关系。"""

    source: str = Field(min_length=1, max_length=120)
    target: str = Field(min_length=1, max_length=120)
    predicate: str = Field(min_length=1, max_length=40)
    summary: str = Field(min_length=1, max_length=220)
    confidence: float = Field(ge=0, le=1)
    segment_id: int = Field(gt=0)
    evidence_quote: str = Field(min_length=1, max_length=300)


class ConnectivityAuditDecision(StrictModel):
    """一个人物或势力完成全部提及窗口检查后的裁定。"""

    entity_id: int = Field(gt=0)
    status: Literal["connected", "confirmed_isolated", "ambiguous"]
    reason: str = Field(min_length=2, max_length=200)
    confidence: float = Field(ge=0, le=1)
    relations: list[ConnectivityRelationCandidate] = Field(default_factory=list, max_length=1)


class ConnectivityAuditResult(StrictModel):
    """一批孤立节点专项复审的严格输出。"""

    decisions: list[ConnectivityAuditDecision] = Field(default_factory=list, max_length=12)


class ProviderKeyRequest(BaseModel):
    """保存一个开放平台密钥，接口永远不会返回原值。"""

    provider: Literal["deepseek", "moonshot"]
    api_key: str = Field(min_length=12, max_length=300)


class RecordDraftRequest(BaseModel):
    """使用陈述式任务为世界卡或数据库条目生成候选版本。"""

    provider: Literal["mock", "deepseek", "moonshot"] = "deepseek"
    instruction: str = Field(min_length=4, max_length=1_000)
    max_cost_usd: float = Field(default=0.05, ge=0, le=20)


class RecordRegenerationResult(StrictModel):
    """二次生成只返回草稿，正式记录由用户确认后更新。"""

    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=1_500)
    category: str = Field(min_length=1, max_length=80)
    attributes: dict[str, Any] = Field(default_factory=dict)
    evidence_quotes: list[str] = Field(default_factory=list, min_length=1, max_length=20)


class LayoutNode(StrictModel):
    """一本书在二维或三维关系图中的固定节点位置。"""

    entity_id: int = Field(gt=0)
    x: float = Field(ge=-100_000, le=100_000)
    y: float = Field(ge=-100_000, le=100_000)
    z: float = Field(default=0, ge=-100_000, le=100_000)
    pinned: bool = True


class RelationshipLayoutPatch(BaseModel):
    """批量保存当前关系图布局。"""

    mode: Literal["2d", "3d"]
    nodes: list[LayoutNode] = Field(default_factory=list, max_length=2_000)
