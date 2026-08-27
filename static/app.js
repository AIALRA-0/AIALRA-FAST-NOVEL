// 单页应用只使用浏览器原生能力，减少首版构建与供应链成本。
const state = {
  books: [],
  folders: [],
  providers: [],
  bookId: null,
  overview: null,
  view: "relationships",
  activeLocation: null,
  activeJobId: null,
  jobTimer: null,
  searchQuery: "",
  relationshipGraph: null,
  relationshipCy: null,
  relationshipLabelFrame: null,
  relationshipResizeObserver: null,
  relationshipInitialFitTimer: null,
  relationshipMode: "2d",
  relationshipHover: null,
  timelineMode: "story",
  mapMode: window.localStorage.getItem("novel-atlas-map-mode") === "3d" ? "3d" : "2d",
  mapPresentation: window.localStorage.getItem("novel-atlas-map-presentation") === "evidence" ? "evidence" : "atlas",
  mapLayout: null,
  narrativeMemory: null,
  knowledgeFacets: null,
  concepts: [],
  mapStep: 0,
  mapTimer: null,
  mapPlaybackState: "idle",
  mapPlaybackRunId: 0,
  mapAnimationFrame: null,
  mapMarkerPoint: null,
  mapMarkerPoint3D: null,
  mapPoints: null,
  mapViewport: null,
  mapShowFullRoute: false,
  mapGraph: null,
  mapGraphResizeObserver: null,
  mapLabelFrame: null,
  map3DActor: null,
  map3DNodes: null,
  map3DLinks: null,
  inspectorTarget: null,
  inspectorRequestSerial: 0,
  analysisEstimate: null,
  analysisStartSegment: 0,
  budgetJobId: null,
  benchmarks: [],
  benchmarkCandidates: [],
  benchmarkEditingId: null,
  controlPlane: null,
  controlPlaneBookId: null,
  promptDetail: null,
};

const labels = {
  relationships: ["人物关系", "谁与谁有关，以及关系从何而来"],
  timeline: ["剧情编年", "按故事时间排列，同时保留原文叙事位置"],
  map: ["逻辑地图", "地点、交通方式与主线人物的行动路径"],
  world: ["世界信息", "规则、力量、势力、背景与地理结构"],
  database: ["条目数据库", "可批量检索的物品、技能、属性、参数与术语"],
  quality: ["质量检查", "查看分析进度、证据覆盖和需要人工确认的问题"],
  collaboration: ["协作控制", "查看提示词、规则、模型路由、验收条件和每次运行"],
};

const transportLabels = {
  walk: "步行",
  road: "陆路",
  water: "水路",
  flight: "飞行",
  teleport: "穿越",
  other: "路径不明",
  "": "未说明",
};

const categoryLabels = {
  person: "人物",
  faction: "势力",
  place: "地点",
  creature: "生物",
  other: "其他",
  power: "力量体系",
  background: "故事背景",
  rule: "关键规则",
  geography: "世界范围",
  culture: "文化",
  item: "物品",
  skill: "技能",
  attribute: "属性",
  parameter: "参数",
  term: "术语",
};

const semanticPalette = {
  person: "#0f6cbd",
  place: "#4856a6",
  faction: "#7a3e9d",
  item: "#b46a00",
  skill: "#d18400",
  other: "#6b7280",
  current: "#e66a00",
  conflict: "#c42b1c",
  unknown: "#7a7a76",
  water: "#1976d2",
  road: "#9a6700",
  walk: "#795548",
  flight: "#00838f",
  teleport: "#7b1fa2",
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

// 所有后端文本都先转义，避免导入小说内容成为页面脚本。
function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

// 数据库存储片段序号，页面统一换成可读的真实章节标题。
function chapterForSegment(ordinal) {
  const segment = state.overview?.segments?.find((item) => Number(item.ordinal) === Number(ordinal));
  return segment?.chapter_title || `第 ${Number(ordinal) + 1} 部分`;
}

// 模型账单以美元展示，并明确区分有价格快照和只有令牌统计的任务。
function formatCost(job) {
  if (job.estimated_cost_usd === null || job.estimated_cost_usd === undefined) return "价格待配置";
  const value = Number(job.estimated_cost_usd || 0);
  return value === 0 ? "$0.000000" : `$${value.toFixed(value < 0.01 ? 6 : 4)}`;
}

function stopMapPlayback(cancelMovement = true) {
  clearTimeout(state.mapTimer);
  state.mapTimer = null;
  state.mapPlaybackRunId += 1;
  if (cancelMovement) {
    cancelAnimationFrame(state.mapAnimationFrame);
    state.mapAnimationFrame = null;
  }
  if (state.mapPlaybackState === "playing") state.mapPlaybackState = "paused";
}

function disposeRelationshipGraph() {
  clearTimeout(state.relationshipInitialFitTimer);
  state.relationshipInitialFitTimer = null;
  cancelAnimationFrame(state.relationshipLabelFrame);
  state.relationshipLabelFrame = null;
  state.relationshipResizeObserver?.disconnect();
  state.relationshipResizeObserver = null;
  if (state.relationshipGraph) {
    state.relationshipGraph.pauseAnimation?.();
    state.relationshipGraph._destructor?.();
  }
  state.relationshipCy?.destroy?.();
  state.relationshipCy = null;
  state.relationshipGraph = null;
  state.relationshipHover = null;
}

function disposeMapGraph() {
  cancelAnimationFrame(state.mapLabelFrame);
  state.mapLabelFrame = null;
  state.mapGraphResizeObserver?.disconnect();
  state.mapGraphResizeObserver = null;
  if (state.mapGraph) {
    state.mapGraph.pauseAnimation?.();
    state.mapGraph._destructor?.();
  }
  state.mapGraph = null;
  state.map3DActor = null;
  state.map3DNodes = null;
  state.map3DLinks = null;
  state.mapViewportController = null;
}

function resetMapStateForBook() {
  stopMapPlayback();
  disposeMapGraph();
  state.mapStep = 0;
  state.mapViewport = null;
  state.mapMarkerPoint = null;
  state.mapMarkerPoint3D = null;
  state.activeLocation = null;
  state.mapPlaybackState = "idle";
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const body = response.headers.get("content-type")?.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    const detail = body?.detail?.message || body?.detail || body || "请求失败";
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body;
}

let toastTimer;
function toast(message, error = false) {
  const element = $("#toast");
  element.textContent = message;
  element.className = `toast show${error ? " error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { element.className = "toast"; }, 3600);
}

async function initialize() {
  try {
    [state.books, state.folders, state.providers] = await Promise.all([
      api("/api/books"), api("/api/library/folders"), api("/api/providers"),
    ]);
    renderBookOptions();
    renderProviderOptions();
    if (state.books.length) {
      state.bookId = Number(state.books[0].id);
      $("#book-select").value = String(state.bookId);
      await loadOverview();
    }
  } catch (error) {
    toast(error.message, true);
    $("#view-panel").innerHTML = emptyState("应用没有完成初始化", error.message);
  }
}

function renderBookOptions() {
  const folderById = new Map(state.folders.map((folder) => [Number(folder.id), folder]));
  const pathFor = (folderId) => {
    const names = [];
    const visited = new Set();
    let current = folderById.get(Number(folderId));
    while (current && !visited.has(Number(current.id))) {
      visited.add(Number(current.id));
      names.unshift(current.name);
      current = current.parent_id ? folderById.get(Number(current.parent_id)) : null;
    }
    return names.join(" / ");
  };
  const grouped = new Map();
  state.books.forEach((book) => {
    const label = book.folder_id ? pathFor(book.folder_id) : "未分类";
    if (!grouped.has(label)) grouped.set(label, []);
    grouped.get(label).push(book);
  });
  $("#book-select").innerHTML = [...grouped.entries()].map(([label, books]) =>
    `<optgroup label="${escapeHtml(label)}">${books.map((book) => `<option value="${book.id}">${escapeHtml(book.title)}</option>`).join("")}</optgroup>`
  ).join("");
}

function renderProviderOptions() {
  $("#provider-select").innerHTML = state.providers.map((provider) => {
    const suffix = provider.available ? "" : " · 未配置";
    return `<option value="${escapeHtml(provider.id)}" ${provider.available ? "" : "disabled"}>${escapeHtml(provider.label + suffix)}</option>`;
  }).join("");
  const preferred = state.providers.find((provider) => provider.id === "deepseek" && provider.available)
    || state.providers.find((provider) => provider.id === "moonshot" && provider.available)
    || state.providers.find((provider) => provider.available);
  if (preferred) $("#provider-select").value = preferred.id;
}

async function loadOverview(throughSegment = null, silent = false) {
  if (!state.bookId) return;
  const requestedBookId = state.bookId;
  if (!silent) $("#view-panel").innerHTML = '<div class="loading">正在整理证据与视图…</div>';
  const query = throughSegment === null ? "" : `?through_segment=${throughSegment}`;
  const [overview, benchmarks, benchmarkCandidates, mapLayout, narrativeMemory, knowledgeFacets, concepts] = await Promise.all([
    api(`/api/books/${requestedBookId}/overview${query}`),
    api(`/api/books/${requestedBookId}/benchmarks`),
    api(`/api/books/${requestedBookId}/benchmark-candidates`),
    api(`/api/books/${requestedBookId}/map-layout${query}`),
    api(`/api/books/${requestedBookId}/narrative-memory${query}`),
    api(`/api/books/${requestedBookId}/knowledge-facets`),
    api(`/api/books/${requestedBookId}/concepts?status=&limit=1000`),
  ]);
  if (state.bookId !== requestedBookId) return;
  state.overview = overview;
  state.benchmarks = benchmarks;
  state.benchmarkCandidates = benchmarkCandidates;
  state.mapLayout = mapLayout;
  state.narrativeMemory = narrativeMemory;
  state.knowledgeFacets = knowledgeFacets;
  state.concepts = concepts;
  configureProgress();
  renderHeader();
  renderMetrics();
  renderView();
  renderJobFromOverview();
}

function configureProgress() {
  const slider = $("#progress-slider");
  const overview = state.overview;
  slider.max = Math.max(0, overview.segments.length - 1);
  slider.value = overview.through_segment;
  const segment = overview.segments[overview.through_segment];
  $("#progress-chapter").textContent = segment?.chapter_title || "无章节";
  $("#progress-count").textContent = `${overview.through_segment + 1}/${overview.segments.length}`;
}

function renderHeader() {
  const [title, subtitle] = labels[state.view];
  $("#view-eyebrow").textContent = title;
  $("#book-title").textContent = state.overview.book.title;
  $("#book-subtitle").textContent = `${subtitle} · ${state.overview.book.character_count.toLocaleString()} 字`;
}

function renderMetrics() {
  const overview = state.overview;
  const targetItems = [...overview.entities, ...overview.claims, ...(overview.geography_relations || []), ...overview.events, ...overview.world_notes, ...overview.entries];
  const covered = targetItems.filter((item) => Number(item.evidence_count) > 0).length;
  const narrativeIds = [...overview.events].sort((a, b) => a.narrative_order - b.narrative_order).map((item) => item.id);
  const nonlinear = overview.events.filter((item, index) => narrativeIds[index] !== item.id).length;
  $("#metric-entities").textContent = overview.entities.length;
  $("#metric-persons").textContent = `${overview.entities.filter((item) => item.kind === "person").length} 位人物`;
  $("#metric-events").textContent = overview.events.length;
  $("#metric-flashbacks").textContent = `${nonlinear} 条非线性叙事`;
  $("#metric-evidence").textContent = targetItems.length ? `${Math.round(covered / targetItems.length * 100)}%` : "—";
  const unresolved = Number(overview.quality?.unresolved_merges || 0);
  $("#metric-review").textContent = unresolved;
  $("#metric-review-card").classList.toggle("warning", unresolved > 0);
}

function renderJobFromOverview() {
  const latest = state.overview.analysis_jobs?.[0];
  if (!latest) {
    clearTimeout(state.jobTimer);
    state.activeJobId = null;
    $("#job-panel").hidden = true;
    return;
  }
  state.activeJobId = latest.id;
  renderJob(latest);
  if (["queued", "running", "paused", "quality_checking"].includes(latest.status)) scheduleJobPoll();
}

function renderJob(job) {
  const panel = $("#job-panel");
  const total = Number(job.total_segments || 0);
  const completed = Number(job.completed_segments || 0);
  const reviewOnly = total === 0;
  const structuralIssue = job.status === "needs_review" || (job.status === "completed" && state.overview?.quality?.structural_gate_passed === false);
  const statusLabels = {
    queued: reviewOnly ? "等待全书复核" : "等待开始",
    running: reviewOnly ? "正在整理跨章节信息" : "正在分析整本书",
    quality_checking: "正在执行关系与地图质量门禁",
    paused: "分析已暂停",
    completed: reviewOnly ? "全书复核已完成" : "整本书分析已完成",
    needs_review: "正文抽取完成，质量门禁等待解决",
    failed: "分析遇到问题",
    cancelled: "分析已取消",
  };
  if (structuralIssue) statusLabels.completed = "正文分析完成，结构审查未通过";
  const percent = total ? Math.round(completed / total * 100) : (["completed", "quality_checking", "needs_review"].includes(job.status) ? 100 : 0);
  const structuralTitles = (state.overview?.quality?.issues || [])
    .filter((issue) => issue.level === "error")
    .map((issue) => issue.title)
    .join("、");
  const current = structuralIssue
    ? `需要修复：${structuralTitles || "地图或关系结构不完整"}`
    : job.current_segment?.chapter_title
    ? `当前：${job.current_segment.chapter_title}`
    : reviewOnly
      ? "正文片段已经全部复用，本次只整理跨章节人物、时间和世界信息。"
      : `${completed}/${total} 个章节`;
  const budgetActivity = Number(job.budget_adjustments || 0) ? ` · 自动适配 ${Number(job.budget_adjustments)} 次` : "";
  const usage = `输入 ${Number(job.input_tokens || 0).toLocaleString()} · 缓存命中 ${Number(job.cache_hit_input_tokens || 0).toLocaleString()} · 输出 ${Number(job.output_tokens || 0).toLocaleString()} 令牌 · 本次约 ${formatCost(job)}${budgetActivity}`;
  let actions = "";
  if (["queued", "running"].includes(job.status)) {
    actions = '<button class="button button-quiet job-action" data-action="pause" type="button">暂停</button><button class="button button-danger job-action" data-action="cancel" type="button">取消</button>';
  } else if (job.status === "paused") {
    actions = `${job.budget_status === "blocked" ? '<button class="button button-quiet job-budget" type="button">调整上限</button>' : ""}<button class="button button-primary job-action" data-action="resume" type="button">继续</button><button class="button button-danger job-action" data-action="cancel" type="button">取消</button>`;
  } else if (job.status === "failed") {
    actions = '<button class="button button-primary job-action" data-action="retry" type="button">重试失败片段</button>';
  } else if (job.status === "needs_review") {
    actions = '<button class="button button-primary job-quality" type="button">查看并解决</button>';
  }
  panel.hidden = false;
  panel.classList.toggle("failed", job.status === "failed");
  panel.classList.toggle("warning", structuralIssue);
  panel.innerHTML = `<div class="job-copy"><strong>${escapeHtml(statusLabels[job.status] || job.status)} · ${percent}%</strong><progress class="job-progress" max="100" value="${percent}"></progress><span>${escapeHtml(job.error || current)}</span><small>${escapeHtml(usage)}</small></div><div class="job-actions">${actions}</div>`;
  $$(".job-action").forEach((button) => button.addEventListener("click", () => controlAnalysisJob(button.dataset.action)));
  $(".job-budget")?.addEventListener("click", () => openBudgetEditor(job));
  $(".job-quality")?.addEventListener("click", () => {
    state.view = "quality";
    $(".nav-item.active")?.classList.remove("active");
    $(".nav-item[data-view='quality']")?.classList.add("active");
    renderHeader();
    renderView();
  });
}

function openBudgetEditor(job) {
  state.budgetJobId = Number(job.id);
  state.analysisEstimate = null;
  $("#analysis-budget").value = Number(job.max_cost_usd || 0.5).toFixed(2);
  $("#analysis-estimate").classList.remove("over");
  $("#analysis-estimate").innerHTML = `<strong>任务已用 ${escapeHtml(formatCost(job))}</strong><br>当前自动范围 $${Number(job.max_cost_usd || 0).toFixed(2)} · 输入 ${Number(job.input_tokens || 0).toLocaleString()}/${Number(job.max_input_tokens || 0).toLocaleString()} · 输出 ${Number(job.output_tokens || 0).toLocaleString()}/${Number(job.max_output_tokens || 0).toLocaleString()}<br>保存后任务保持暂停，点击“继续”会恢复自动适配。`;
  $("#analysis-reestimate").hidden = true;
  document.querySelector('label[for="analysis-review-mode"]')?.setAttribute("hidden", "");
  $("#analysis-review-mode").hidden = true;
  $("#analysis-start").textContent = "保存新上限";
  $("#analysis-start").disabled = false;
  $("#analysis-dialog").showModal();
}

function scheduleJobPoll() {
  clearTimeout(state.jobTimer);
  if (!state.activeJobId) return;
  state.jobTimer = setTimeout(pollJob, 1000);
}

async function pollJob() {
  if (!state.activeJobId) return;
  const requestedJobId = state.activeJobId;
  const requestedBookId = state.bookId;
  try {
    const job = await api(`/api/jobs/${requestedJobId}`);
    if (state.activeJobId !== requestedJobId || state.bookId !== requestedBookId) return;
    renderJob(job);
    if (["queued", "running", "paused", "quality_checking"].includes(job.status)) {
      scheduleJobPoll();
    } else {
      await loadOverview(Number($("#progress-slider").value), true);
    }
  } catch (error) {
    toast(error.message, true);
  }
}

async function controlAnalysisJob(action) {
  try {
    const job = await api(`/api/jobs/${state.activeJobId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
    renderJob(job);
    if (["resume", "retry"].includes(action)) scheduleJobPoll();
  } catch (error) {
    toast(error.message, true);
  }
}

function renderView() {
  disposeRelationshipGraph();
  disposeMapGraph();
  if (state.view !== "map") stopMapPlayback();
  if (state.view === "map") closeInspector();
  renderHeader();
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === state.view));
  const renderers = {
    relationships: renderRelationships,
    timeline: renderTimeline,
    map: renderMap,
    world: renderWorld,
    database: renderDatabase,
    quality: renderQuality,
    collaboration: renderCollaboration,
  };
  renderers[state.view]();
}

function panelHead(title, description, legend = "") {
  return `<header class="panel-head"><div><h2>${escapeHtml(title)}</h2><p>${escapeHtml(description)}</p></div>${legend}</header>`;
}

function emptyState(title, message) {
  return `<div class="empty-state"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(message)}</p></div>`;
}

// 正式关系图始终返回全部已确认节点和关系。缩放只调整标签密度，不能删除事实。
function relationshipDataset(allEntities, allClaims) {
  const degree = new Map(allEntities.map((node) => [node.id, 0]));
  allClaims.forEach((claim) => {
    degree.set(claim.source_entity_id, (degree.get(claim.source_entity_id) || 0) + 1);
    degree.set(claim.target_entity_id, (degree.get(claim.target_entity_id) || 0) + 1);
  });
  return { entities: allEntities, claims: allClaims, degree };
}

function renderRelationships() {
  const relationshipEntities = state.overview.entities.filter((item) => ["person", "faction"].includes(item.kind));
  const relationshipEntityIds = new Set(relationshipEntities.map((node) => node.id));
  const allClaims = state.overview.claims.filter((item) => relationshipEntityIds.has(item.source_entity_id) && relationshipEntityIds.has(item.target_entity_id));
  const connectedIds = new Set(allClaims.flatMap((claim) => [claim.source_entity_id, claim.target_entity_id]));
  const allEntities = relationshipEntities.filter((node) => connectedIds.has(node.id));
  const connectivityReviews = state.overview.connectivity_reviews || [];
  const confirmedIsolated = connectivityReviews.filter((item) => item.status === "confirmed_isolated");
  const unresolvedConnectivity = connectivityReviews.filter((item) => ["pending", "ambiguous"].includes(item.status));
  const { entities, claims } = relationshipDataset(allEntities, allClaims);
  const legend = '<div class="legend"><span><i></i>人物</span><span><i class="faction"></i>势力</span><span>线条为有证据关系</span></div>';
  const mergeCandidates = state.overview.merge_candidates || [];
  const identitySummary = state.overview.identity_summary || {};
  const automaticCount = Number(identitySummary.merge || 0) + Number(identitySummary.separate || 0);
  const mergeReview = `<details class="merge-review${mergeCandidates.length ? " warning" : ""}"><summary>身份系统已自动裁决 ${automaticCount} 组 · ${mergeCandidates.length} 组证据不足，可选复核</summary>${mergeCandidates.length ? `<div class="merge-list">${mergeCandidates.map((candidate) => `<div class="merge-candidate"><span><strong>${escapeHtml(candidate.left_name)}</strong> 与 <strong>${escapeHtml(candidate.right_name)}</strong><br>${escapeHtml(candidate.reason)} · 系统未自动合并</span><div class="merge-candidate-actions"><button class="button button-quiet merge-choice" data-keep="${candidate.left_entity_id}" data-remove="${candidate.right_entity_id}" type="button">确认为 ${escapeHtml(candidate.left_name)}</button><button class="button button-quiet merge-choice" data-keep="${candidate.right_entity_id}" data-remove="${candidate.left_entity_id}" type="button">确认为 ${escapeHtml(candidate.right_name)}</button><button class="button button-danger merge-reject" data-id="${candidate.id}" type="button">确认是两个人</button></div></div>`).join("")}</div>` : '<p class="merge-complete">没有遗留给用户的身份判断。</p>'}</details>`;
  const isolatedSection = confirmedIsolated.length || unresolvedConnectivity.length ? `<section class="connectivity-review-section">
    ${confirmedIsolated.length ? `<details><summary>已确认孤立 ${confirmedIsolated.length} 个</summary><div class="connectivity-card-grid">${confirmedIsolated.map((item) => `<button class="connectivity-card target-button" data-type="entity" data-id="${item.entity_id}" type="button"><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.reason)}</span><small>已扫描 ${item.scanned_segment_count} 个章节 · ${item.mention_count} 次提及</small></button>`).join("")}</div></details>` : ""}
    ${unresolvedConnectivity.length ? `<details class="warning" open><summary>等待关系复审 ${unresolvedConnectivity.length} 个</summary><p>这些节点不会混入正式关系图。系统会先自动复审，仍有歧义时可在质量检查中手动解决。</p></details>` : ""}
  </section>` : "";
  if (!allEntities.length) {
    $("#view-panel").innerHTML = panelHead("人物关系网", "点击节点查看身份、别名和原文证据。", legend) + mergeReview + emptyState("还没有通过复审的关系", "孤立节点会先扫描全部提及窗口，确认关系或确认孤立后再进入相应区域。") + isolatedSection;
    bindMergeReview();
    bindTargets();
    return;
  }
  const fallbackEntities = entities.map((node) => `<li><button class="text-link target-button" data-type="entity" data-id="${node.id}">${escapeHtml(node.name)}</button> · ${escapeHtml(categoryLabels[node.kind] || node.kind)}</li>`).join("");
  const fallbackClaims = claims.map((claim) => `<li><button class="text-link target-button" data-type="claim" data-id="${claim.id}">${escapeHtml(claim.source_name)} —${escapeHtml(claim.predicate)}→ ${escapeHtml(claim.target_name)}</button></li>`).join("");
  const interactionHint = state.relationshipMode === "3d" ? "拖动空白处旋转，拖动节点固定位置，滚轮缩放；悬停会突出当前关系。" : "拖动空白处平移，拖动节点固定位置，滚轮缩放；悬停会突出当前关系。";
  $("#view-panel").innerHTML = `${panelHead("人物关系网", interactionHint, legend)}${mergeReview}
    <div class="graph-toolbar" aria-label="关系图控制">
      <div class="graph-control-groups"><div class="segmented-control"><button class="graph-mode${state.relationshipMode === "3d" ? " active" : ""}" data-mode="3d" type="button">3D 探索</button><button class="graph-mode${state.relationshipMode === "2d" ? " active" : ""}" data-mode="2d" type="button">2D 平面</button></div></div>
      <div class="graph-actions"><button id="graph-fit" class="button button-quiet" type="button">适合窗口</button><button id="graph-reset" class="button button-quiet" type="button">重置视角</button><button id="graph-unpin" class="button button-quiet" type="button">重新自动布局</button></div>
      <span>全量展示 ${entities.length} 个节点 · ${claims.length} 条关系</span>
    </div>
    <div class="force-graph-shell"><div id="relationship-graph" class="force-graph" role="img" aria-label="可旋转和拖动的人物关系图"></div><div id="relationship-labels" class="relationship-labels" aria-hidden="true"></div><div id="relationship-focus" class="relationship-focus">把鼠标移到人物或关系线上，查看单独关系。</div></div>
    <details class="fallback-list"><summary>查看全部人物与关系的文字列表</summary><h3>人物与势力</h3><ul>${fallbackEntities}</ul><h3>全部关系</h3><ul>${fallbackClaims}</ul></details>${isolatedSection}`;
  bindTargets();
  bindMergeReview();
  createRelationshipGraph(entities, claims);
}

function createRelationshipGraph(entities, claims) {
  if (state.relationshipMode === "2d") {
    createRelationshipGraph2D(entities, claims);
    return;
  }
  const host = $("#relationship-graph");
  if (!host || typeof window.ForceGraph3D !== "function") {
    host.innerHTML = emptyState("关系图组件没有载入", "仍可使用下方文字列表查看人物和证据。");
    return;
  }
  const pairCounts = new Map();
  const pairIndexes = new Map();
  const links = claims.map((claim) => {
    const pairKey = [claim.source_entity_id, claim.target_entity_id].sort((a, b) => a - b).join(":");
    pairCounts.set(pairKey, (pairCounts.get(pairKey) || 0) + 1);
    const index = pairIndexes.get(pairKey) || 0;
    pairIndexes.set(pairKey, index + 1);
    return {
      ...claim,
      source: claim.source_entity_id,
      target: claim.target_entity_id,
      pairKey,
      pairIndex: index,
    };
  });
  links.forEach((link) => {
    const count = pairCounts.get(link.pairKey) || 1;
    link.curvature = count === 1 ? 0 : (link.pairIndex - (count - 1) / 2) * 0.18;
  });
  const degree = new Map(entities.map((node) => [node.id, 0]));
  links.forEach((link) => {
    degree.set(link.source_entity_id, (degree.get(link.source_entity_id) || 0) + 1);
    degree.set(link.target_entity_id, (degree.get(link.target_entity_id) || 0) + 1);
  });
  const columns = Math.max(3, Math.ceil(Math.sqrt(entities.length)));
  const savedLayouts = new Map((state.overview.relationship_layouts || [])
    .filter((item) => item.mode === state.relationshipMode)
    .map((item) => [Number(item.entity_id), item]));
  const nodes = entities.map((node, index) => {
    const saved = savedLayouts.get(Number(node.id));
    const x = saved ? Number(saved.x) : (index % columns - (columns - 1) / 2) * 118;
    const y = saved ? Number(saved.y) : (Math.floor(index / columns) - Math.floor(entities.length / columns) / 2) * 104;
    const z = state.relationshipMode === "2d" ? 0 : saved ? Number(saved.z) : (((node.id * 37) % 9) - 4) * 42;
    return {
      ...node,
      degree: degree.get(node.id) || 0,
      x, y, z,
      fx: saved?.pinned ? x : undefined,
      fy: saved?.pinned ? y : undefined,
      fz: state.relationshipMode === "2d" ? 0 : saved?.pinned ? z : undefined,
    };
  });
  const graphData = { nodes, links };
  const graph = window.ForceGraph3D({ controlType: "orbit" })(host);
  let nodeDragActive = false;
  state.relationshipGraph = graph;
  state.relationshipHover = { node: null, link: null };

  const linkedToHovered = (link, hoveredId) => {
    const sourceId = typeof link.source === "object" ? link.source.id : link.source;
    const targetId = typeof link.target === "object" ? link.target.id : link.target;
    return sourceId === hoveredId || targetId === hoveredId;
  };
  const currentNodeIds = () => {
    const hoveredNode = state.relationshipHover?.node;
    const hoveredLink = state.relationshipHover?.link;
    if (hoveredNode) {
      const result = new Set([hoveredNode.id]);
      links.filter((link) => linkedToHovered(link, hoveredNode.id)).forEach((link) => {
        result.add(typeof link.source === "object" ? link.source.id : link.source);
        result.add(typeof link.target === "object" ? link.target.id : link.target);
      });
      return result;
    }
    if (hoveredLink) {
      return new Set([
        typeof hoveredLink.source === "object" ? hoveredLink.source.id : hoveredLink.source,
        typeof hoveredLink.target === "object" ? hoveredLink.target.id : hoveredLink.target,
      ]);
    }
    return null;
  };
  const refreshFocus = () => {
    const focus = $("#relationship-focus");
    const hoveredNode = state.relationshipHover?.node;
    const hoveredLink = state.relationshipHover?.link;
    if (!focus) return;
    if (hoveredLink) {
      focus.innerHTML = `<strong>${escapeHtml(hoveredLink.source_name)} —${escapeHtml(hoveredLink.predicate)}→ ${escapeHtml(hoveredLink.target_name)}</strong><span>${escapeHtml(hoveredLink.summary)} · 点击关系线查看原文证据</span>`;
      return;
    }
    if (hoveredNode) {
      const related = claims.filter((claim) => claim.source_entity_id === hoveredNode.id || claim.target_entity_id === hoveredNode.id);
      focus.innerHTML = `<strong>${escapeHtml(hoveredNode.name)}</strong><span>${escapeHtml(hoveredNode.summary)} · ${related.length} 条可核验关系</span>`;
      return;
    }
    focus.textContent = "把鼠标移到人物或关系线上，查看单独关系。";
  };

  graph
    .graphData(graphData)
    .numDimensions(state.relationshipMode === "2d" ? 2 : 3)
    .backgroundColor("#f7f7f5")
    .showNavInfo(false)
    .nodeLabel("")
    .nodeVal((node) => 3.5 + Number(node.importance) * 7 + Math.min(node.degree, 10) * 0.22)
    .nodeResolution(20)
    .nodeOpacity(0.96)
    .nodeColor((node) => {
      const visibleIds = currentNodeIds();
      if (visibleIds && !visibleIds.has(node.id)) return "#dededa";
      if (state.relationshipHover?.node?.id === node.id) return semanticPalette.current;
      return semanticPalette[node.kind] || semanticPalette.other;
    })
    .linkColor((link) => {
      if (state.relationshipHover?.link === link) return semanticPalette.current;
      const hoveredNode = state.relationshipHover?.node;
      if (hoveredNode) return linkedToHovered(link, hoveredNode.id) ? "#222222" : "#dededa";
      if (state.relationshipHover?.link) return "#dededa";
      return "#92928e";
    })
    .linkOpacity(0.48)
    .linkWidth((link) => state.relationshipHover?.link === link || (state.relationshipHover?.node && linkedToHovered(link, state.relationshipHover.node.id)) ? 3.2 : 1.2)
    .linkCurvature((link) => link.curvature)
    .linkDirectionalArrowLength(3.1)
    .linkDirectionalArrowRelPos(0.76)
    .linkDirectionalArrowColor((link) => state.relationshipHover?.link === link ? semanticPalette.current : "#666663")
    .linkHoverPrecision(3)
    .onNodeHover((node) => {
      state.relationshipHover = { node, link: null };
      host.style.cursor = node ? "pointer" : "grab";
      graph.refresh();
      refreshFocus();
    })
    .onLinkHover((link) => {
      state.relationshipHover = { node: null, link };
      host.style.cursor = link ? "pointer" : "grab";
      graph.refresh();
      refreshFocus();
    })
    .onNodeClick((node) => openInspector("entity", node.id))
    .onLinkClick((link) => openInspector("claim", link.id))
    .onNodeDrag(() => {
      if (nodeDragActive) return;
      nodeDragActive = true;
      graph.enableNavigationControls(false);
    })
    .onNodeDragEnd((node) => {
      node.vx = 0;
      node.vy = 0;
      node.vz = 0;
      node.fx = node.x;
      node.fy = node.y;
      node.fz = state.relationshipMode === "2d" ? 0 : node.z;
      requestAnimationFrame(() => {
        if (state.relationshipGraph !== graph) return;
        nodeDragActive = false;
        graph.enableNavigationControls(true);
        graph.refresh();
        saveRelationshipPositions([node]);
      });
    })
    .onBackgroundClick(() => {
      state.relationshipHover = { node: null, link: null };
      graph.refresh();
      refreshFocus();
    })
    .cooldownTicks(180)
    .warmupTicks(Math.min(80, 20 + Math.floor(nodes.length / 2)));
  graph.d3VelocityDecay(0.46);
  graph.d3Force("charge")?.strength(nodes.length > 50 ? -420 : -520).distanceMax(750);
  graph.d3Force("link")?.distance((link) => 105 + Math.min(45, ((link.source?.degree || 0) + (link.target?.degree || 0)) * 2));

  const resize = () => {
    const width = Math.max(320, host.clientWidth);
    const height = Math.max(480, Math.min(690, Math.round(window.innerHeight * 0.64)));
    graph.width(width).height(height);
  };
  resize();
  state.relationshipResizeObserver = new ResizeObserver(resize);
  state.relationshipResizeObserver.observe(host);
  const controls = graph.controls();
  controls.enableRotate = state.relationshipMode === "3d";
  controls.enablePan = true;
  controls.enableZoom = true;
  controls.enableDamping = true;
  controls.dampingFactor = 0.11;
  controls.rotateSpeed = 0.52;
  controls.zoomSpeed = 0.68;
  controls.panSpeed = 0.62;
  controls.screenSpacePanning = true;
  controls.minDistance = 90;
  controls.maxDistance = 2600;
  let userNavigated = false;
  let initialFitDone = false;
  let layoutShaped = false;
  const markUserNavigation = () => { userNavigated = true; };
  host.addEventListener("pointerdown", markUserNavigation, { passive: true });
  host.addEventListener("wheel", markUserNavigation, { passive: true });
  host.addEventListener("touchstart", markUserNavigation, { passive: true });
  const fitInitialLayout = () => {
    if (initialFitDone || userNavigated || state.relationshipGraph !== graph) return;
    initialFitDone = true;
    graph.zoomToFit(520, 72);
  };
  state.relationshipInitialFitTimer = setTimeout(fitInitialLayout, 900);
  graph.onEngineStop(() => {
    if (!layoutShaped) {
      nodes.forEach((node) => {
        if (Number.isFinite(node.fx) && Number.isFinite(node.fy)) return;
        node.x *= state.relationshipMode === "2d" ? 1.7 : 1.55;
        node.y *= 0.92;
        node.z *= state.relationshipMode === "2d" ? 0 : 0.72;
      });
      layoutShaped = true;
      graph.refresh();
    }
    fitInitialLayout();
  });

  let lastLabelDraw = 0;
  const drawLabels = (time) => {
    if (state.relationshipGraph !== graph) return;
    if (time - lastLabelDraw > 45) {
      renderRelationshipLabels(graph, nodes, links, currentNodeIds());
      lastLabelDraw = time;
    }
    state.relationshipLabelFrame = requestAnimationFrame(drawLabels);
  };
  state.relationshipLabelFrame = requestAnimationFrame(drawLabels);

  $$(".graph-mode").forEach((button) => button.addEventListener("click", () => {
    state.relationshipMode = button.dataset.mode;
    disposeRelationshipGraph();
    renderRelationships();
  }));
  $("#graph-fit")?.addEventListener("click", () => graph.zoomToFit(520, 76));
  $("#graph-reset")?.addEventListener("click", () => {
    const position = state.relationshipMode === "2d" ? { x: 0, y: 0, z: 700 } : { x: 260, y: 150, z: 620 };
    graph.cameraPosition(position, { x: 0, y: 0, z: 0 }, 500);
    setTimeout(() => graph.zoomToFit(420, 76), 520);
  });
  $("#graph-unpin")?.addEventListener("click", async () => {
    try {
      await api(`/api/books/${state.bookId}/relationship-layout/${state.relationshipMode}`, { method: "DELETE" });
      nodes.forEach((node) => {
        node.fx = undefined;
        node.fy = undefined;
        node.fz = state.relationshipMode === "2d" ? 0 : undefined;
      });
      state.overview.relationship_layouts = (state.overview.relationship_layouts || [])
        .filter((item) => item.mode !== state.relationshipMode);
      graph.d3ReheatSimulation();
      toast("固定位置已清除，关系图正在重新排布。");
    } catch (error) {
      toast(error.message, true);
    }
  });
}

// 二维阅读模式使用 fCoSE 的碰撞、组件打包和增量约束，密集图不再依赖手写坐标修补。
function createRelationshipGraph2D(entities, claims) {
  const host = $("#relationship-graph");
  if (!host || typeof window.cytoscape !== "function") {
    host.innerHTML = emptyState("二维关系图组件没有载入", "仍可切换到三维模式或使用下方文字列表。");
    return;
  }
  $("#relationship-labels")?.replaceChildren();
  const degree = new Map(entities.map((node) => [Number(node.id), 0]));
  claims.forEach((claim) => {
    degree.set(Number(claim.source_entity_id), (degree.get(Number(claim.source_entity_id)) || 0) + 1);
    degree.set(Number(claim.target_entity_id), (degree.get(Number(claim.target_entity_id)) || 0) + 1);
  });
  const savedLayouts = new Map((state.overview.relationship_layouts || [])
    .filter((item) => item.mode === "2d")
    .map((item) => [Number(item.entity_id), item]));
  const elements = [
    ...entities.map((node) => ({
      group: "nodes",
      data: {
        id: `n${node.id}`,
        entityId: Number(node.id),
        label: node.name,
        summary: node.summary,
        kind: node.kind,
        importance: Number(node.importance || 0),
        degree: degree.get(Number(node.id)) || 0,
      },
      position: savedLayouts.has(Number(node.id)) ? {
        x: Number(savedLayouts.get(Number(node.id)).x),
        y: Number(savedLayouts.get(Number(node.id)).y),
      } : undefined,
    })),
    ...claims.map((claim) => ({
      group: "edges",
      data: {
        id: `e${claim.id}`,
        claimId: Number(claim.id),
        source: `n${claim.source_entity_id}`,
        target: `n${claim.target_entity_id}`,
        predicate: claim.predicate,
        summary: claim.summary,
        sourceName: claim.source_name,
        targetName: claim.target_name,
        confidence: Number(claim.confidence || 0),
      },
    })),
  ];
  const cy = window.cytoscape({
    container: host,
    elements,
    minZoom: 0.16,
    maxZoom: 3.2,
    boxSelectionEnabled: false,
    autoungrabify: false,
    style: [
      {
        selector: "node",
        style: {
          width: "mapData(degree, 0, 16, 30, 54)",
          height: "mapData(degree, 0, 16, 30, 54)",
          "background-color": semanticPalette.person,
          "border-width": 4,
          "border-color": "#ffffff",
          label: "data(label)",
          color: "#183b32",
          "font-size": 12,
          "font-weight": 700,
          "text-valign": "bottom",
          "text-halign": "center",
          "text-margin-y": 12,
          "text-wrap": "wrap",
          "text-max-width": 118,
          "text-background-color": "#ffffff",
          "text-background-opacity": 0.94,
          "text-background-padding": 5,
          "text-background-shape": "roundrectangle",
          "overlay-opacity": 0,
        },
      },
      { selector: "node[kind = 'faction']", style: { "background-color": semanticPalette.faction, shape: "round-rectangle" } },
      {
        selector: "edge",
        style: {
          width: "mapData(confidence, 0, 1, 1, 2.4)",
          "line-color": "#91a79f",
          "target-arrow-color": "#718d83",
          "target-arrow-shape": "triangle",
          "arrow-scale": 0.68,
          "curve-style": "bezier",
          opacity: 0.58,
          label: "",
          "font-size": 10,
          color: "#36574d",
          "text-background-color": "#ffffff",
          "text-background-opacity": 0.96,
          "text-background-padding": 3,
          "text-rotation": "autorotate",
          "overlay-opacity": 0,
        },
      },
      { selector: ".muted", style: { opacity: 0.09, "text-opacity": 0 } },
      { selector: "node.focused", style: { "border-color": semanticPalette.current, "border-width": 6, "z-index": 20 } },
      { selector: "edge.focused", style: { opacity: 1, width: 3.2, "line-color": semanticPalette.person, "target-arrow-color": semanticPalette.current, label: "data(predicate)", "z-index": 18 } },
    ],
  });
  state.relationshipCy = cy;
  const fixedNodeConstraint = [...savedLayouts.entries()]
    .filter(([, item]) => Boolean(item.pinned))
    .map(([entityId, item]) => ({ nodeId: `n${entityId}`, position: { x: Number(item.x), y: Number(item.y) } }));
  const runLayout = (randomize = fixedNodeConstraint.length === 0) => {
    const layoutName = typeof cy.layout({ name: "fcose" }).run === "function" ? "fcose" : "cose";
    cy.layout(layoutName === "fcose" ? {
      name: "fcose",
      quality: "proof",
      randomize,
      animate: true,
      animationDuration: 720,
      fit: true,
      padding: 72,
      nodeRepulsion: (node) => 7600 + Number(node.data("degree") || 0) * 420,
      idealEdgeLength: (edge) => 118 + Math.min(52, Number(edge.source().data("degree") || 0) * 3),
      edgeElasticity: 0.28,
      nestingFactor: 0.12,
      gravity: 0.2,
      gravityRange: 3.8,
      numIter: 2800,
      tile: true,
      tilingPaddingVertical: 46,
      tilingPaddingHorizontal: 62,
      fixedNodeConstraint,
    } : { name: "cose", animate: true, fit: true, padding: 72, nodeRepulsion: 7600, idealEdgeLength: 130 })
      .run();
  };
  runLayout();
  const focus = $("#relationship-focus");
  const clearFocus = () => {
    cy.elements().removeClass("muted focused");
    if (focus) focus.textContent = "把鼠标移到人物或关系线上，查看单独关系。";
  };
  cy.on("mouseover", "node", (event) => {
    const node = event.target;
    const neighborhood = node.closedNeighborhood();
    cy.elements().difference(neighborhood).addClass("muted");
    neighborhood.addClass("focused");
    if (focus) focus.innerHTML = `<strong>${escapeHtml(node.data("label"))}</strong><span>${escapeHtml(node.data("summary"))} · ${node.connectedEdges().length} 条可核验关系</span>`;
  });
  cy.on("mouseout", "node", clearFocus);
  cy.on("mouseover", "edge", (event) => {
    const edge = event.target;
    cy.elements().not(edge).not(edge.connectedNodes()).addClass("muted");
    edge.addClass("focused");
    edge.connectedNodes().addClass("focused");
    if (focus) focus.innerHTML = `<strong>${escapeHtml(edge.data("sourceName"))} —${escapeHtml(edge.data("predicate"))}→ ${escapeHtml(edge.data("targetName"))}</strong><span>${escapeHtml(edge.data("summary"))} · 点击关系线查看原文证据</span>`;
  });
  cy.on("mouseout", "edge", clearFocus);
  cy.on("tap", "node", (event) => openInspector("entity", Number(event.target.data("entityId"))));
  cy.on("tap", "edge", (event) => openInspector("claim", Number(event.target.data("claimId"))));
  cy.on("dragfree", "node", (event) => {
    const node = event.target;
    const position = node.position();
    saveRelationshipPositions([{ id: Number(node.data("entityId")), x: position.x, y: position.y, z: 0 }]);
  });
  const resize = () => cy.resize();
  state.relationshipResizeObserver = new ResizeObserver(resize);
  state.relationshipResizeObserver.observe(host);
  $$(".graph-mode").forEach((button) => button.addEventListener("click", () => {
    state.relationshipMode = button.dataset.mode;
    disposeRelationshipGraph();
    renderRelationships();
  }));
  $("#graph-fit")?.addEventListener("click", () => cy.fit(cy.elements(), 72));
  $("#graph-reset")?.addEventListener("click", () => { cy.reset(); cy.fit(cy.elements(), 72); });
  $("#graph-unpin")?.addEventListener("click", async () => {
    try {
      await api(`/api/books/${state.bookId}/relationship-layout/2d`, { method: "DELETE" });
      state.overview.relationship_layouts = (state.overview.relationship_layouts || []).filter((item) => item.mode !== "2d");
      fixedNodeConstraint.splice(0);
      runLayout(true);
      toast("固定位置已清除，二维关系图正在重新排布。");
    } catch (error) {
      toast(error.message, true);
    }
  });
}

async function saveRelationshipPositions(nodes) {
  try {
    await api(`/api/books/${state.bookId}/relationship-layout`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mode: state.relationshipMode,
        nodes: nodes.map((node) => ({
          entity_id: Number(node.id),
          x: Number(node.x || 0),
          y: Number(node.y || 0),
          z: state.relationshipMode === "2d" ? 0 : Number(node.z || 0),
          pinned: true,
        })),
      }),
    });
  } catch (error) {
    toast(`节点位置没有保存：${error.message}`, true);
  }
}

function renderRelationshipLabels(graph, nodes, links, visibleIds) {
  const layer = $("#relationship-labels");
  const host = $("#relationship-graph");
  if (!layer || !host) return;
  const hoveredId = state.relationshipHover?.node?.id;
  const candidates = nodes
    .filter((node) => Number.isFinite(node.x) && Number.isFinite(node.y))
    .filter((node) => !visibleIds || visibleIds.has(node.id))
    .sort((a, b) => (a.id === hoveredId ? -1 : b.id === hoveredId ? 1 : (b.degree + b.importance * 4) - (a.degree + a.importance * 4)))
    .slice(0, visibleIds ? nodes.length : Math.min(30, nodes.length));
  const boxes = [{ left: 12, right: host.clientWidth - 12, top: host.clientHeight - 78, bottom: host.clientHeight }];
  const labels = [];
  for (const node of candidates) {
    const point = graph.graph2ScreenCoords(node.x, node.y, node.z || 0);
    if (!point || point.x < 6 || point.y < 6 || point.x > host.clientWidth - 6 || point.y > host.clientHeight - 6) continue;
    const width = Math.max(44, [...node.name].length * 14 + 18);
    const offsets = [[0, 28], [0, -28], [width / 2 + 18, 0], [-width / 2 - 18, 0], [width / 2 + 14, 24], [-width / 2 - 14, -24]];
    let placement = null;
    for (const [dx, dy] of offsets) {
      const box = { left: point.x + dx - width / 2, right: point.x + dx + width / 2, top: point.y + dy - 13, bottom: point.y + dy + 13 };
      const outside = box.left < 6 || box.right > host.clientWidth - 6 || box.top < 6 || box.bottom > host.clientHeight - 6;
      const collides = boxes.some((placed) => !(box.right < placed.left || box.left > placed.right || box.bottom < placed.top || box.top > placed.bottom));
      if (!outside && (!collides || node.id === hoveredId)) {
        placement = { x: point.x + dx, y: point.y + dy, box };
        break;
      }
    }
    if (!placement) continue;
    boxes.push(placement.box);
    labels.push(`<span class="relationship-label${node.id === hoveredId ? " active" : ""}" style="left:${placement.x}px;top:${placement.y}px">${escapeHtml(node.name)}</span>`);
  }
  layer.innerHTML = labels.join("");
}

function bindMergeReview() {
  $$(".merge-choice").forEach((button) => button.addEventListener("click", async () => {
    try {
      await api(`/api/books/${state.bookId}/entities/merge`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keep_entity_id: Number(button.dataset.keep), remove_entity_id: Number(button.dataset.remove), reason: "用户确认同一实体" }),
      });
      toast("人物资料已合并，关系和证据已经转移。");
      await loadOverview(Number($("#progress-slider").value));
    } catch (error) {
      toast(error.message, true);
    }
  }));
  $$(".merge-reject").forEach((button) => button.addEventListener("click", async () => {
    try {
      await api(`/api/merge-candidates/${button.dataset.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "rejected" }),
      });
      await loadOverview(Number($("#progress-slider").value));
    } catch (error) {
      toast(error.message, true);
    }
  }));
}

// 编年、地图、详情和播放按钮都从同一有序步骤读取数据。
// 旧数据库无需重跑；overview.events 是兼容回退，不会生成第二套顺序。
function storyMapSteps() {
  return state.overview?.story_map_steps || state.overview?.events || [];
}

function eventNarrativeText(event) {
  const memory = state.narrativeMemory?.recent_scenes?.find((item) => Number(item.id) === Number(event?.id));
  return memory?.narrative_text || event?.summary || "";
}

function renderTimeline() {
  const events = storyMapSteps();
  if (!events.length) {
    $("#view-panel").innerHTML = panelHead("剧情编年史", "从上往下按故事发生时间排列。") + emptyState("还没有可核验事件", "事件必须带原文引文；时间无法确定时会明确标成未知。");
    return;
  }
  const storyOrdered = [...events].sort((left, right) => Number(left.story_order) - Number(right.story_order) || left.narrative_order - right.narrative_order);
  const narrativeOrdered = [...events].sort((left, right) => left.narrative_order - right.narrative_order || left.id - right.id);
  const ordered = state.timelineMode === "story" ? storyOrdered : narrativeOrdered;
  const narrativeIndex = new Map(narrativeOrdered.map((event, index) => [event.id, index]));
  const storyIndex = new Map(storyOrdered.map((event, index) => [event.id, index]));
  const phaseLabels = { main: "当前主线", flashback: "回忆或插叙", dream: "梦境", prophecy: "预言", parallel: "同时支线", unknown: "叙事层级待确认" };
  const cards = ordered.map((event, displayIndex) => {
    const nonlinear = narrativeIndex.get(event.id) !== storyIndex.get(event.id) || event.narrative_phase !== "main";
    const participants = event.participants.map((item) => item.name).join("、");
    return `<article class="timeline-item">
      <div class="timeline-time">${escapeHtml(state.timelineMode === "story" ? event.temporal_value || "时间未知" : chapterForSegment(event.first_segment))}</div><div class="timeline-dot" aria-hidden="true"></div>
      <div class="timeline-card target-button" data-type="event" data-id="${event.id}" tabindex="0" role="button">
        <h3>${escapeHtml(event.title)}</h3><p>${escapeHtml(eventNarrativeText(event))}</p>
        <div class="chip-row">${event.location_name ? `<span class="chip blue">⌖ ${escapeHtml(event.location_name)}</span>` : ""}${participants ? `<span class="chip">${escapeHtml(participants)}</span>` : ""}${nonlinear ? `<span class="chip amber">${escapeHtml(phaseLabels[event.narrative_phase] || "非线性叙事")} · ${escapeHtml(chapterForSegment(event.first_segment))}</span>` : ""}<span class="chip">证据 ${event.evidence_count}</span><span class="chip">${state.timelineMode === "story" ? `编年第 ${displayIndex + 1} 位` : `原文第 ${displayIndex + 1} 位`}</span></div>
      </div>
    </article>`;
  }).join("");
  const conflictCount = (state.overview.time_conflicts || []).length;
  const conflictNotice = conflictCount ? `<div class="timeline-warning">${conflictCount} 条互相冲突的时间约束已经隔离，没有参与当前排序。</div>` : "";
  const toolbar = `<div class="timeline-toolbar"><div class="segmented-control"><button class="timeline-mode${state.timelineMode === "story" ? " active" : ""}" data-mode="story" type="button">故事编年</button><button class="timeline-mode${state.timelineMode === "narrative" ? " active" : ""}" data-mode="narrative" type="button">原文顺序</button></div><span>两种顺序独立保存，回忆不会再冒充当前事件。</span></div>`;
  const openThreads = (state.narrativeMemory?.open_threads || []).filter((item) => item.status === "open").slice(0, 8);
  const characterStates = (state.narrativeMemory?.character_states || []).filter((item) => item.goal || item.states?.length).slice(0, 8);
  const memoryPanel = openThreads.length || characterStates.length ? `<details class="narrative-memory"><summary>当前承接记忆 · ${openThreads.length} 条未闭合线索</summary>${openThreads.length ? `<article><strong>尚未解决</strong><p>${openThreads.map((item) => escapeHtml(item.title)).join(" · ")}</p></article>` : ""}${characterStates.length ? `<article><strong>人物当前状态</strong><p>${characterStates.map((item) => `${escapeHtml(item.name)}：${escapeHtml(item.goal || item.states?.join("、") || "状态已记录")}${item.location_name ? `，位于${escapeHtml(item.location_name)}` : ""}`).join("<br>")}</p></article>` : ""}</details>` : "";
  $("#view-panel").innerHTML = panelHead("剧情编年史", "故事编年由无环时间约束计算，原文顺序只表示作者何时讲到这件事。") + toolbar + conflictNotice + memoryPanel + `<div class="timeline">${cards}</div>`;
  $$(".timeline-mode").forEach((button) => button.addEventListener("click", () => {
    state.timelineMode = button.dataset.mode;
    renderTimeline();
  }));
  bindTargets();
}

const geographyLabels = {
  north: "北", south: "南", east: "东", west: "西",
  northeast: "东北", northwest: "西北", southeast: "东南", southwest: "西南",
  inside: "位于内部", contains: "包含", near: "邻近", upstream: "上游", downstream: "下游",
};

// 后端快照不可用时使用确定性的黄金角投影；它只保证稳定和可读，不伪造方位。
function stableTopologyFallback(locations) {
  const ordered = [...locations].sort((left, right) => Number(left.id) - Number(right.id));
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  return new Map(ordered.map((location, index) => {
    const radius = 92 * Math.sqrt(index + 1);
    const angle = index * goldenAngle + (Number(location.id) % 17) * 0.013;
    return [Number(location.id), {
      x: 620 + Math.cos(angle) * radius,
      y: 420 + Math.sin(angle) * radius * 0.72,
      fixed: true,
      source: "stable_topology_projection",
    }];
  }));
}

// 逻辑地图使用与关系图相同的成熟约束布局；原文明示的方位会成为硬相对位置约束。
function layoutMapLocations(locations, journey, relations) {
  const snapshotNodes = new Map((state.mapLayout?.nodes || []).map((node) => [Number(node.id), node]));
  if (locations.length && locations.every((location) => snapshotNodes.has(Number(location.id)))) {
    return new Map(locations.map((location) => {
      const point = snapshotNodes.get(Number(location.id));
      return [Number(location.id), {
        x: Number(point.x), y: Number(point.y), fixed: true,
        z: Number(point.z || 0), source: point.coordinate_source,
        evidenceLevel: point.evidence_level,
      }];
    }));
  }
  if (typeof window.cytoscape !== "function" || locations.length < 2) {
    return stableTopologyFallback(locations);
  }
  try {
    const firstVisit = new Map();
    journey.forEach((event, index) => {
      if (event.location_entity_id !== null && !firstVisit.has(Number(event.location_entity_id))) {
        firstVisit.set(Number(event.location_entity_id), index);
      }
    });
    const edgeKeys = new Set();
    const locationIds = new Set(locations.map((location) => Number(location.id)));
    const orderedLocations = [...locations].sort((left, right) => {
      const leftVisit = firstVisit.get(Number(left.id)) ?? Number.MAX_SAFE_INTEGER;
      const rightVisit = firstVisit.get(Number(right.id)) ?? Number.MAX_SAFE_INTEGER;
      return leftVisit - rightVisit || Number(left.first_segment) - Number(right.first_segment) || Number(left.id) - Number(right.id);
    });
    const initialPositions = stableTopologyFallback(orderedLocations);
    const elements = locations.map((location) => ({
      group: "nodes",
      position: initialPositions.get(Number(location.id)),
      data: {
        id: `p${location.id}`,
        width: Math.max(64, [...location.name].length * 14 + 30),
        visit: firstVisit.get(Number(location.id)) ?? 100000,
      },
    }));
    relations.forEach((relation, index) => {
      if (!locationIds.has(Number(relation.source_entity_id)) || !locationIds.has(Number(relation.target_entity_id))) return;
      const source = `p${relation.source_entity_id}`;
      const target = `p${relation.target_entity_id}`;
      const key = [source, target].sort().join(":");
      if (source === target || edgeKeys.has(key)) return;
      edgeKeys.add(key);
      elements.push({ group: "edges", data: { id: `m${index}`, source, target } });
    });
    const relativePlacementConstraint = [];
    const addHorizontal = (leftId, rightId) => relativePlacementConstraint.push({ left: `p${leftId}`, right: `p${rightId}`, gap: 122 });
    const addVertical = (topId, bottomId) => relativePlacementConstraint.push({ top: `p${topId}`, bottom: `p${bottomId}`, gap: 96 });
    relations.forEach((relation) => {
      const source = Number(relation.source_entity_id);
      const target = Number(relation.target_entity_id);
      if (!locationIds.has(source) || !locationIds.has(target)) return;
      if (relation.relative_position === "north") addVertical(source, target);
      if (relation.relative_position === "south") addVertical(target, source);
      if (relation.relative_position === "east") addHorizontal(target, source);
      if (relation.relative_position === "west") addHorizontal(source, target);
      if (relation.relative_position === "northeast") { addVertical(source, target); addHorizontal(target, source); }
      if (relation.relative_position === "northwest") { addVertical(source, target); addHorizontal(source, target); }
      if (relation.relative_position === "southeast") { addVertical(target, source); addHorizontal(target, source); }
      if (relation.relative_position === "southwest") { addVertical(target, source); addHorizontal(source, target); }
    });
    const cy = window.cytoscape({
      headless: true,
      styleEnabled: true,
      elements,
      style: [
        { selector: "node", style: { width: "data(width)", height: 54, shape: "round-rectangle" } },
        { selector: "edge", style: { "curve-style": "bezier" } },
      ],
    });
    const largeMap = locations.length > 90;
    const layoutOptions = largeMap ? {
      // 百级地点图使用 Cytoscape 内置 CoSE。它对大量弱连接分量更稳定，再由下方投影恢复
      // 原文明示的东南西北约束，避免 fCoSE 在极端分量上生成无效内部网格。
      name: "cose",
      animate: false,
      fit: false,
      randomize: false,
      nodeRepulsion: () => 9800,
      idealEdgeLength: () => 150,
      edgeElasticity: () => 0.22,
      gravity: 0.12,
      numIter: 2600,
      componentSpacing: 150,
    } : {
      name: "fcose",
      quality: "proof",
      randomize: false,
      animate: false,
      fit: false,
      nodeRepulsion: 9800,
      idealEdgeLength: 150,
      edgeElasticity: 0.22,
      nestingFactor: 0.1,
      gravity: 0.16,
      gravityRange: 4.2,
      numIter: 3200,
      tile: true,
      tilingPaddingVertical: 68,
      tilingPaddingHorizontal: 82,
      packComponents: false,
      relativePlacementConstraint,
    };
    cy.layout(layoutOptions).run();
    const projectedPositions = new Map(locations.map((location) => [
      Number(location.id),
      { ...cy.getElementById(`p${location.id}`).position() },
    ]));
    if (largeMap) {
      const directionalVectors = {
        north: [0, -118], south: [0, 118], east: [154, 0], west: [-154, 0],
        northeast: [126, -96], northwest: [-126, -96], southeast: [126, 96], southwest: [-126, 96],
      };
      // 每轮只移动一小段，保留 CoSE 的整体拓扑，同时把可验证方位投影回正确象限。
      for (let pass = 0; pass < 28; pass += 1) {
        relations.forEach((relation) => {
          const source = projectedPositions.get(Number(relation.source_entity_id));
          const target = projectedPositions.get(Number(relation.target_entity_id));
          const vector = directionalVectors[relation.relative_position];
          if (!source || !target || !vector) return;
          const errorX = target.x + vector[0] - source.x;
          const errorY = target.y + vector[1] - source.y;
          source.x += errorX * 0.08;
          source.y += errorY * 0.08;
          target.x -= errorX * 0.08;
          target.y -= errorY * 0.08;
        });
      }
    }
    const rawPoints = locations.map((location) => ({ location, position: projectedPositions.get(Number(location.id)) }));
    cy.destroy();
    const xs = rawPoints.map((item) => item.position.x);
    const ys = rawPoints.map((item) => item.position.y);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    const scaleX = 760 / Math.max(1, maxX - minX);
    const scaleY = 340 / Math.max(1, maxY - minY);
    // 大部头地图保留节点之间的可读距离，画布可以超出视窗并通过拖动、缩放查看。
    // 把整本书的全部地点强行压进 900×470 会让文字重叠，局部行程也无法辨认。
    const scale = Math.max(1, Math.min(scaleX, scaleY));
    const usedWidth = (maxX - minX) * scale;
    const usedHeight = (maxY - minY) * scale;
    const offsetX = usedWidth <= 760 ? (900 - usedWidth) / 2 : 90;
    const offsetY = usedHeight <= 340 ? (470 - usedHeight) / 2 : 90;
    return new Map(rawPoints.map(({ location, position }) => [
      Number(location.id),
      { x: offsetX + (position.x - minX) * scale, y: offsetY + (position.y - minY) * scale, fixed: false },
    ]));
  } catch (error) {
    console.warn("地图约束布局失败，已使用保守布局。", error);
    return stableTopologyFallback(locations);
  }
}

// 地图只在画布上显示短地名，完整名称保留在 title 和下方地点详情中。
function mapDisplayName(name) {
  const characters = [...String(name || "")];
  return characters.length > 12 ? `${characters.slice(0, 10).join("")}…` : characters.join("");
}

// 地名从四个方向选择空位，并用底色保持线路穿过时仍然可读。
function mapLabelPlacements(locations, points, journey, currentLocationId) {
  const pointValues = [...points.values()];
  const limits = {
    left: Math.min(...pointValues.map((point) => point.x)) - 90,
    right: Math.max(...pointValues.map((point) => point.x)) + 90,
    top: Math.min(...pointValues.map((point) => point.y)) - 70,
    bottom: Math.max(...pointValues.map((point) => point.y)) + 70,
  };
  const occupied = locations.map((location) => {
    const point = points.get(location.id);
    return { left: point.x - 25, right: point.x + 25, top: point.y - 25, bottom: point.y + 25 };
  });
  const firstVisit = new Map();
  journey.forEach((event, index) => {
    if (event.location_entity_id !== null && !firstVisit.has(Number(event.location_entity_id))) {
      firstVisit.set(Number(event.location_entity_id), index);
    }
  });
  const ordered = [...locations].sort((left, right) => {
    const leftCurrent = Number(left.id) === Number(currentLocationId) ? 1 : 0;
    const rightCurrent = Number(right.id) === Number(currentLocationId) ? 1 : 0;
    return rightCurrent - leftCurrent
      || Number(right.importance || 0) - Number(left.importance || 0)
      || (firstVisit.get(Number(left.id)) ?? 1_000_000) - (firstVisit.get(Number(right.id)) ?? 1_000_000)
      || Number(left.id) - Number(right.id);
  });
  const result = new Map();
  ordered.forEach((location) => {
    const point = points.get(location.id);
    const width = Math.max(52, [...mapDisplayName(location.name)].length * 14 + 18);
    const candidates = [
      { x: point.x, y: point.y + 47, anchor: "middle" },
      { x: point.x, y: point.y - 40, anchor: "middle" },
      { x: point.x + 34, y: point.y + 4, anchor: "start" },
      { x: point.x - 34, y: point.y + 4, anchor: "end" },
      { x: point.x + 32, y: point.y - 31, anchor: "start" },
      { x: point.x - 32, y: point.y - 31, anchor: "end" },
      { x: point.x + 32, y: point.y + 35, anchor: "start" },
      { x: point.x - 32, y: point.y + 35, anchor: "end" },
    ];
    let selected = null;
    let selectedScore = Number.POSITIVE_INFINITY;
    for (const candidate of candidates) {
      const left = candidate.anchor === "middle" ? candidate.x - width / 2 : candidate.anchor === "start" ? candidate.x : candidate.x - width;
      // Keep a small gutter because SVG text metrics and rounded label boxes do
      // not resolve to exactly the same pixels at every browser zoom level.
      const box = { left: left - 8, right: left + width + 8, top: candidate.y - 21, bottom: candidate.y + 12 };
      const outside = box.left < limits.left || box.right > limits.right || box.top < limits.top || box.bottom > limits.bottom;
      const collisions = occupied.filter((placed) => !(box.right < placed.left || box.left > placed.right || box.bottom < placed.top || box.top > placed.bottom)).length;
      const score = collisions * 1000 + (outside ? 100 : 0);
      if (score < selectedScore) {
        selected = { ...candidate, width, box };
        selectedScore = score;
      }
      if (score === 0) break;
    }
    const isCurrent = Number(location.id) === Number(currentLocationId);
    const visible = selectedScore === 0 || isCurrent;
    if (visible) occupied.push(selected.box);
    result.set(location.id, { ...selected, visible });
  });
  return result;
}

function shortenedRoute(start, end, bendSeed) {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const length = Math.max(1, Math.hypot(dx, dy));
  const ux = dx / length;
  const uy = dy / length;
  const sx = start.x + ux * 29;
  const sy = start.y + uy * 29;
  const ex = end.x - ux * 31;
  const ey = end.y - uy * 31;
  const bend = ((bendSeed % 5) - 2) * 9;
  const cx = (sx + ex) / 2 - uy * bend;
  const cy = (sy + ey) / 2 + ux * bend;
  return { sx, sy, ex, ey, cx, cy };
}

function renderMap() {
  const allLocations = state.overview.entities.filter((item) => item.kind === "place");
  const people = state.overview.entities.filter((item) => item.kind === "person");
  const journey = storyMapSteps();
  const allRoutes = state.overview.routes || [];
  const routeByEventId = new Map(allRoutes.filter((route) => route.event_id !== null).map((route) => [Number(route.event_id), route]));
  const allGeographyRelations = state.overview.geography_relations || [];
  const mappedLocationIds = new Set(
    journey.filter((event) => event.location_entity_id !== null).map((event) => Number(event.location_entity_id)),
  );
  const journeyLocationIds = new Set(mappedLocationIds);
  allRoutes.forEach((route) => {
    if (route.from_id !== null) mappedLocationIds.add(Number(route.from_id));
    if (route.to_id !== null) mappedLocationIds.add(Number(route.to_id));
  });
  if (journeyLocationIds.size) {
    allGeographyRelations.forEach((relation) => {
      const sourceId = Number(relation.source_entity_id);
      const targetId = Number(relation.target_entity_id);
      if (journeyLocationIds.has(sourceId) || journeyLocationIds.has(targetId)) {
        mappedLocationIds.add(sourceId);
        mappedLocationIds.add(targetId);
      }
    });
  } else {
    allGeographyRelations.forEach((relation) => {
      mappedLocationIds.add(Number(relation.source_entity_id));
      mappedLocationIds.add(Number(relation.target_entity_id));
    });
    [...allLocations]
      .sort((left, right) => Number(right.importance) - Number(left.importance) || left.first_segment - right.first_segment)
      .slice(0, Math.max(0, 40 - mappedLocationIds.size))
      .forEach((location) => mappedLocationIds.add(Number(location.id)));
  }
  const locations = allLocations.filter((location) => mappedLocationIds.has(Number(location.id)));
  const geographyRelations = allGeographyRelations.filter(
    (relation) => mappedLocationIds.has(Number(relation.source_entity_id)) && mappedLocationIds.has(Number(relation.target_entity_id)),
  );
  const rawRouteTopology = allRoutes.filter((route) =>
    route.from_id !== null && route.to_id !== null
      && mappedLocationIds.has(Number(route.from_id)) && mappedLocationIds.has(Number(route.to_id))
  );
  // 同一对地点可能在多个章节反复往返。地图底层只画一条拓扑边，逐步行程仍按编年完整保留。
  const topologyByPair = new Map();
  rawRouteTopology.forEach((route) => {
    const key = [Number(route.from_id), Number(route.to_id)].sort((left, right) => left - right).join(":");
    const existing = topologyByPair.get(key);
    if (!existing) {
      topologyByPair.set(key, { ...route, occurrence_count: 1, transports: [route.transport] });
      return;
    }
    existing.occurrence_count += 1;
    if (route.transport && !existing.transports.includes(route.transport)) existing.transports.push(route.transport);
    existing.confidence = Math.max(Number(existing.confidence || 0), Number(route.confidence || 0));
  });
  const routeTopology = [...topologyByPair.values()];
  const selectedProtagonist = state.overview.protagonist?.id;
  const protagonistPicker = `<div class="protagonist-picker"><label for="protagonist-select">人物轨迹层</label><select id="protagonist-select"><option value="auto" ${state.overview.protagonist_auto ? "selected" : ""}>自动判断${selectedProtagonist ? `：${escapeHtml(state.overview.protagonist.name)}` : ""}</option>${people.map((person) => `<option value="${person.id}" ${!state.overview.protagonist_auto && Number(selectedProtagonist) === Number(person.id) ? "selected" : ""}>${escapeHtml(person.name)}</option>`).join("")}</select><span>选择只改变人物标记，故事步骤始终与编年一致</span></div>`;
  const legend = '<div class="legend"><span>━ 水路</span><span>┅ 陆路</span><span>╌ 穿越</span></div>';
  if (!locations.length) {
    $("#view-panel").innerHTML = panelHead("逻辑地图与故事编年", "地图按照故事编年逐步显示地点、人物和事件。", legend) + protagonistPicker + emptyState("还没有可核验地点", "编年步骤仍然完整保留，地点证据补齐后会自动进入地图。");
    bindProtagonistPicker();
    return;
  }
  const topologyConstraints = [
    ...geographyRelations,
    ...routeTopology.map((route) => ({
      source_entity_id: Number(route.from_id),
      target_entity_id: Number(route.to_id),
      relative_position: "near",
      confidence: Number(route.confidence || 0.7),
    })),
  ];
  const points = layoutMapLocations(locations, journey, topologyConstraints);
  const currentLocationId = journey[state.mapStep]?.location_entity_id;
  const labelPlacements = mapLabelPlacements(locations, points, journey, currentLocationId);
  state.mapPoints = points;
  const pointValues = [...points.values()];
  state.mapBounds = {
    minX: Math.min(...pointValues.map((point) => point.x)) - 110,
    maxX: Math.max(...pointValues.map((point) => point.x)) + 110,
    minY: Math.min(...pointValues.map((point) => point.y)) - 90,
    maxY: Math.max(...pointValues.map((point) => point.y)) + 90,
  };
  state.mapStep = Math.max(0, Math.min(state.mapStep, Math.max(0, journey.length - 1)));
  const paper = `<rect class="map-paper-plane" x="${state.mapBounds.minX}" y="${state.mapBounds.minY}" width="${state.mapBounds.maxX - state.mapBounds.minX}" height="${state.mapBounds.maxY - state.mapBounds.minY}"></rect>`;
  const visibleLocationIds = new Set(locations.map((location) => Number(location.id)));
  const semanticRegions = state.mapPresentation === "atlas" ? (state.mapLayout?.regions || []).map((region, index) => {
    const regionNodes = (region.node_ids || []).filter((nodeId) => visibleLocationIds.has(Number(nodeId)));
    if (regionNodes.length < 3 || !(region.hull || []).length) return "";
    const path = region.hull.map((point, pointIndex) => `${pointIndex ? "L" : "M"} ${Number(point.x)} ${Number(point.y)}`).join(" ");
    return `<g class="semantic-region region-${index % 6}"><path d="${path} Z"></path><title>${escapeHtml(region.label)}；语义区域不代表原文明示边界</title></g>`;
  }).join("") : "";
  const geography = geographyRelations.slice(0, 36).map((relation) => {
    const start = points.get(relation.source_entity_id);
    const end = points.get(relation.target_entity_id);
    if (!start || !end) return "";
    const line = shortenedRoute(start, end, relation.id);
    const label = geographyLabels[relation.relative_position] || relation.relative_position;
    return `<g class="geography-relation" data-source="${relation.source_entity_id}" data-target="${relation.target_entity_id}"><path d="M ${line.sx} ${line.sy} L ${line.ex} ${line.ey}"></path><title>${escapeHtml(relation.source_name)}相对${escapeHtml(relation.target_name)}：${escapeHtml(label)} · ${escapeHtml(relation.summary)}</title></g>`;
  }).join("");
  const topology = routeTopology.slice(0, 50).map((route, index) => {
    const start = points.get(Number(route.from_id));
    const end = points.get(Number(route.to_id));
    if (!start || !end || Number(route.from_id) === Number(route.to_id)) return "";
    const line = shortenedRoute(start, end, 5000 + index);
    const labels = route.transports.map((transport) => transportLabels[transport] || transport).filter(Boolean);
    const label = labels.join("、") || "移动";
    const occurrences = route.occurrence_count > 1 ? ` · 编年中出现 ${route.occurrence_count} 次` : "";
    return `<g class="route-topology" data-source="${route.from_id}" data-target="${route.to_id}"><path d="M ${line.sx} ${line.sy} Q ${line.cx} ${line.cy} ${line.ex} ${line.ey}"></path><title>${escapeHtml(route.from_name || "未知地点")}到${escapeHtml(route.to_name || "未知地点")}：${escapeHtml(label)}${escapeHtml(occurrences)}</title></g>`;
  }).join("");
  let previousLocatedEvent = null;
  const routeParts = [];
  journey.forEach((event, step) => {
    if (event.location_entity_id === null) return;
    const previous = previousLocatedEvent;
    previousLocatedEvent = event;
    if (!previous) return;
    const start = points.get(previous.location_entity_id);
    const end = points.get(event.location_entity_id);
    if (!start || !end || Number(previous.location_entity_id) === Number(event.location_entity_id)) return;
    const line = shortenedRoute(start, end, step);
    const leg = routeByEventId.get(Number(event.id));
    const routeTransport = leg?.transport && leg.transport !== "未说明" ? leg.transport : event.transport;
    const gapClass = leg?.gap_status === "unknown_path" ? " unknown_path" : "";
    const routeLabel = leg?.gap_status === "unknown_path" ? "路径有缺口" : transportLabels[routeTransport] || routeTransport || "路线";
    routeParts.push(`<g class="journey-route${gapClass}" data-step="${step}" tabindex="0" role="button" aria-label="跳到编年第${step + 1}步"><path class="route ${escapeHtml(routeTransport)}" d="M ${line.sx} ${line.sy} Q ${line.cx} ${line.cy} ${line.ex} ${line.ey}"></path><title>${escapeHtml(previous.location_name || "未知地点")}到${escapeHtml(event.location_name || "未知地点")}：${escapeHtml(routeLabel)} · ${escapeHtml(event.title)}${leg?.gap_status === "unknown_path" ? "；中间路径缺少原文说明" : ""}</title></g>`);
  });
  const routes = routeParts.join("");
  const nodes = locations.map((location) => {
    const point = points.get(location.id);
    const label = labelPlacements.get(location.id);
    const rectX = label.anchor === "middle" ? label.x - label.width / 2 : label.anchor === "start" ? label.x - 5 : label.x - label.width + 5;
    const labelClass = label.visible ? "map-node-label" : "map-node-label map-node-label-collided";
    return `<g class="map-node graph-node" data-location="${location.id}" tabindex="0" role="button" aria-label="跳到${escapeHtml(location.name)}发生的剧情"><circle cx="${point.x}" cy="${point.y}" r="20"></circle><g class="${labelClass}"><line class="map-label-stem" x1="${point.x}" y1="${point.y}" x2="${label.x}" y2="${label.y - 4}"></line><rect class="map-label-bg" x="${rectX}" y="${label.y - 17}" width="${label.width}" height="24" rx="7"></rect><text x="${label.x}" y="${label.y}" text-anchor="${label.anchor}">${escapeHtml(mapDisplayName(location.name))}</text></g><title>${escapeHtml(location.name)} · ${escapeHtml(location.summary)}</title></g>`;
  }).join("");
  const firstEvent = journey[state.mapStep];
  const firstPoint = firstEvent?.location_entity_id !== null ? points.get(firstEvent.location_entity_id) : null;
  const initialMarker = firstPoint ? `transform="translate(${firstPoint.x} ${firstPoint.y})"` : "hidden";
  const initials = [...(state.overview.protagonist?.name || "主")].slice(-1).join("");
  const controls = journey.length ? `<div class="journey-controls"><button id="map-prev" class="button button-quiet" type="button">上一步</button><button id="map-play" class="button button-primary" type="button">播放编年</button><button id="map-next" class="button button-quiet" type="button">下一步</button><input id="map-step-slider" type="range" min="0" max="${journey.length - 1}" value="${state.mapStep}" aria-label="选择故事编年步骤"><strong id="map-step-count">${state.mapStep + 1}/${journey.length}</strong><button id="map-route-scope" class="button button-quiet route-scope" type="button">${state.mapShowFullRoute ? "只看当前附近" : "显示完整路线"}</button></div>` : "";
  const directionalKinds = new Set(["north", "south", "east", "west", "northeast", "northwest", "southeast", "southwest", "upstream", "downstream"]);
  const directionalCount = geographyRelations.filter((relation) => directionalKinds.has(relation.relative_position)).length;
  const containmentCount = geographyRelations.filter((relation) => ["inside", "contains"].includes(relation.relative_position)).length;
  const directionCoverage = directionalCount / Math.max(1, locations.length);
  const projectionCount = [...points.values()].filter((point) => point.source === "stable_topology_projection").length;
  const positionNote = directionalCount
    ? `已使用 ${directionalCount} 条原文方向关系约束方位；${projectionCount} 个地点只使用稳定拓扑坐标。`
    : containmentCount
      ? `原文没有东南西北坐标；地图保持故事拓扑，三维纵深只使用 ${containmentCount} 条包含关系。`
      : routeTopology.length
        ? `原文没有东南西北坐标；地图根据 ${routeTopology.length} 条移动连接形成稳定拓扑，不把坐标冒充真实方位。`
        : "原文尚未提供可核验方位；地点使用稳定拓扑投影，坐标不代表真实方向。";
  const mapModes = `<div class="map-view-toolbar"><div class="map-toolbar-groups"><div class="segmented-control" aria-label="地图表现"><button class="map-presentation${state.mapPresentation === "atlas" ? " active" : ""}" data-presentation="atlas" type="button">语义世界图</button><button class="map-presentation${state.mapPresentation === "evidence" ? " active" : ""}" data-presentation="evidence" type="button">证据逻辑图</button></div><div class="segmented-control" aria-label="地图维度"><button class="map-mode${state.mapMode === "2d" ? " active" : ""}" data-mode="2d" type="button">2D</button><button class="map-mode${state.mapMode === "3d" ? " active" : ""}" data-mode="3d" type="button">3D</button></div></div><span>${state.mapMode === "3d" ? "拖动旋转，滚轮缩放；纵深只表示有证据的包含层级。" : state.mapPresentation === "atlas" ? "彩色区域只整理故事拓扑，不代表原文明示边界。" : "只显示能够回到原文的方向、包含和移动关系。"}</span></div>`;
  const viewportControls = `<div class="map-viewport-controls" aria-label="地图缩放"><button id="map-zoom-out" type="button" aria-label="缩小地图">−</button><button id="map-zoom-reset" type="button">复位</button><button id="map-zoom-in" type="button" aria-label="放大地图">＋</button></div>`;
  const mapCanvas = state.mapMode === "3d"
    ? `<div class="map-3d-shell"><div id="map-3d" class="map-3d" role="img" aria-label="可旋转、缩放并逐步播放的三维故事地图"></div><div id="map-3d-labels" class="map-3d-labels" aria-hidden="true"></div><div class="map-3d-axis" aria-hidden="true">${directionalCount ? "<span>北 ↑</span>" : "<span>平面方位未知</span>"}<span>纵深＝有证据的包含层级</span>${directionalCount ? "<span>东 →</span>" : "<span>拓扑坐标</span>"}</div></div>`
    : `<svg class="map-svg" viewBox="0 0 900 470" role="img" aria-label="可拖动、缩放并逐步播放的二维故事地图"><defs><marker id="route-arrow" markerUnits="userSpaceOnUse" markerWidth="3.2" markerHeight="3.2" refX="2.8" refY="1.6" orient="auto"><path d="M0,0 L3.2,1.6 L0,3.2 z" fill="context-stroke"></path></marker></defs>${paper}${semanticRegions}${geography}${topology}${routes}${nodes}<g id="journey-avatar" class="journey-avatar" ${initialMarker}><circle r="15"></circle><text y="4">${escapeHtml(initials)}</text></g></svg>`;
  $("#view-panel").innerHTML = `${panelHead("逻辑地图与故事编年", "二维和三维共用同一个故事步骤、地点证据和播放状态。", legend)}<p class="map-position-note">${escapeHtml(positionNote)}</p>${protagonistPicker}${controls}<div class="journey-layout"><div class="map-stage">${mapModes}${viewportControls}${mapCanvas}</div><section id="map-event-card" class="map-event-card" aria-live="polite"></section></div><section id="map-location-panel" class="map-location-panel" aria-live="polite"></section>`;
  bindProtagonistPicker();
  if (state.mapMode === "3d") {
    createMapGraph3D(locations, geographyRelations, routeTopology, journey, routeByEventId, points);
  } else {
    bindMapViewport();
  }
  $$(".map-mode").forEach((button) => button.addEventListener("click", () => {
    const nextMode = button.dataset.mode;
    if (nextMode === state.mapMode) return;
    state.mapMode = nextMode;
    window.localStorage.setItem("novel-atlas-map-mode", nextMode);
    disposeMapGraph();
    renderMap();
  }));
  $$(".map-presentation").forEach((button) => button.addEventListener("click", () => {
    const nextPresentation = button.dataset.presentation;
    if (nextPresentation === state.mapPresentation) return;
    state.mapPresentation = nextPresentation;
    window.localStorage.setItem("novel-atlas-map-presentation", nextPresentation);
    renderMap();
  }));
  if (!journey.length) {
    $("#map-event-card").innerHTML = emptyState("还没有连续行程", "事件需要同时包含地点和主线人物，才能在地图上逐步移动。");
    $$(".map-node").forEach((node) => {
      const activate = () => renderMapLocationDetails(Number(node.dataset.location));
      node.addEventListener("click", activate);
      node.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") activate(); });
    });
    renderMapLocationDetails(Number(locations[0]?.id));
    return;
  }
  $$(".journey-route").forEach((route) => route.addEventListener("click", () => setMapStep(Number(route.dataset.step))));
  $$(".map-node").forEach((node) => {
    const activate = () => {
      const locationId = Number(node.dataset.location);
      state.activeLocation = locationId;
      const step = journey.findIndex((event) => Number(event.location_entity_id) === locationId);
      if (step >= 0) setMapStep(step);
      renderMapLocationDetails(locationId);
    };
    node.addEventListener("click", activate);
    node.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") activate(); });
  });
  $("#map-prev").addEventListener("click", () => setMapStep(state.mapStep - 1));
  $("#map-next").addEventListener("click", () => setMapStep(state.mapStep + 1));
  $("#map-step-slider").addEventListener("input", (event) => setMapStep(Number(event.target.value)));
  $("#map-play").addEventListener("click", toggleMapPlayback);
  $("#map-route-scope").addEventListener("click", () => {
    state.mapShowFullRoute = !state.mapShowFullRoute;
    $("#map-route-scope").textContent = state.mapShowFullRoute ? "只看当前附近" : "显示完整行程";
    setMapStep(state.mapStep, false);
    if (state.mapShowFullRoute) state.mapViewportController?.fitAll();
    else if (state.mapMarkerPoint) state.mapViewportController?.focus(state.mapMarkerPoint, true, true);
  });
  requestAnimationFrame(() => setMapStep(state.mapStep, false));
  requestAnimationFrame(() => {
    const currentLocation = journey[state.mapStep]?.location_entity_id;
    if (currentLocation !== null && currentLocation !== undefined) renderMapLocationDetails(Number(currentLocation));
    else renderMapLocationDetails(null);
  });
}

// 三维地图复用二维地图已经验证过的方位坐标。Z 轴只表示原文明示的“包含/位于内部”，
// 不使用随机深度，也不根据行程顺序伪造地理方位。
function mapContainmentDepths(locations, geographyRelations) {
  const locationIds = new Set(locations.map((location) => Number(location.id)));
  const depths = new Map(locations.map((location) => [Number(location.id), 0]));
  for (let pass = 0; pass < Math.min(12, locations.length); pass += 1) {
    let changed = false;
    geographyRelations.forEach((relation) => {
      const source = Number(relation.source_entity_id);
      const target = Number(relation.target_entity_id);
      if (!locationIds.has(source) || !locationIds.has(target)) return;
      let inner = null;
      let outer = null;
      if (relation.relative_position === "inside") { inner = source; outer = target; }
      if (relation.relative_position === "contains") { inner = target; outer = source; }
      if (inner === null || outer === null) return;
      const next = Math.min(6, (depths.get(outer) || 0) + 1);
      if (next > (depths.get(inner) || 0)) {
        depths.set(inner, next);
        changed = true;
      }
    });
    if (!changed) break;
  }
  return depths;
}

function createMapGraph3D(locations, geographyRelations, routeTopology, journey, routeByEventId, points) {
  const host = $("#map-3d");
  if (!host || typeof window.ForceGraph3D !== "function") {
    if (host) host.innerHTML = emptyState("三维组件没有载入", "已保留当前编年步骤；切换到二维地图仍可继续阅读。");
    return;
  }
  const pointValues = [...points.values()];
  const centerX = (Math.min(...pointValues.map((point) => point.x)) + Math.max(...pointValues.map((point) => point.x))) / 2;
  const centerY = (Math.min(...pointValues.map((point) => point.y)) + Math.max(...pointValues.map((point) => point.y))) / 2;
  const depths = mapContainmentDepths(locations, geographyRelations);
  const nodes = locations.map((location) => {
    const point = points.get(Number(location.id));
    const x = (point.x - centerX) * 0.72;
    const y = -(point.y - centerY) * 0.72;
    const depth = Number.isFinite(Number(point.z)) ? Number(point.z) / 90 : (depths.get(Number(location.id)) || 0);
    const z = depth * 86;
    return {
      ...location,
      id: `place:${location.id}`,
      locationId: Number(location.id),
      mapPoint: point,
      x, y, z, fx: x, fy: y, fz: z,
      depth,
      visible: true,
      active: false,
    };
  });
  const nodeByLocation = new Map(nodes.map((node) => [node.locationId, node]));
  const links = [];
  geographyRelations.forEach((relation) => {
    if (!nodeByLocation.has(Number(relation.source_entity_id)) || !nodeByLocation.has(Number(relation.target_entity_id))) return;
    links.push({
      id: `geo:${relation.id}`,
      source: `place:${relation.source_entity_id}`,
      target: `place:${relation.target_entity_id}`,
      kind: "geography",
      label: geographyLabels[relation.relative_position] || relation.relative_position,
      summary: relation.summary,
      sourceLocationId: Number(relation.source_entity_id),
      targetLocationId: Number(relation.target_entity_id),
      visible: true,
    });
  });
  routeTopology.forEach((route, index) => {
    if (!nodeByLocation.has(Number(route.from_id)) || !nodeByLocation.has(Number(route.to_id)) || Number(route.from_id) === Number(route.to_id)) return;
    links.push({
      id: `topology:${index}:${route.from_id}:${route.to_id}`,
      source: `place:${route.from_id}`,
      target: `place:${route.to_id}`,
      kind: "topology",
      label: route.transports.map((transport) => transportLabels[transport] || transport).filter(Boolean).join("、") || "移动",
      transport: route.transports.find(Boolean) || "road",
      sourceLocationId: Number(route.from_id),
      targetLocationId: Number(route.to_id),
      visible: true,
    });
  });
  let previousLocated = null;
  journey.forEach((event, step) => {
    if (event.location_entity_id === null) return;
    if (previousLocated !== null && Number(previousLocated.location_entity_id) !== Number(event.location_entity_id)) {
      const leg = routeByEventId.get(Number(event.id));
      links.push({
        id: `journey:${step}:${event.id}`,
        source: `place:${previousLocated.location_entity_id}`,
        target: `place:${event.location_entity_id}`,
        kind: "journey",
        step,
        label: leg?.gap_status === "unknown_path" ? "路径有缺口" : transportLabels[leg?.transport || event.transport] || "路线",
        transport: leg?.transport || event.transport || "road",
        sourceLocationId: Number(previousLocated.location_entity_id),
        targetLocationId: Number(event.location_entity_id),
        visible: true,
        current: false,
      });
    }
    previousLocated = event;
  });
  const current = journey[state.mapStep];
  const currentNode = current?.location_entity_id === null ? null : nodeByLocation.get(Number(current?.location_entity_id));
  const actor = {
    id: "journey:actor",
    name: state.overview.protagonist?.name || "主线人物",
    kind: "actor",
    x: currentNode?.x || 0,
    y: currentNode?.y || 0,
    z: (currentNode?.z || 0) + 34,
    fx: currentNode?.x || 0,
    fy: currentNode?.y || 0,
    fz: (currentNode?.z || 0) + 34,
    visible: Boolean(currentNode),
  };
  nodes.push(actor);
  state.map3DActor = actor;
  state.map3DNodes = nodes;
  state.map3DLinks = links;
  const graph = window.ForceGraph3D({ controlType: "orbit" })(host);
  state.mapGraph = graph;
  const refresh = () => graph.refresh();
  graph
    .graphData({ nodes, links })
    .numDimensions(3)
    .backgroundColor("#f7f7f5")
    .showNavInfo(false)
    .nodeLabel((node) => node.kind === "actor" ? `<strong>${escapeHtml(node.name)}</strong><br>当前故事步骤` : `<strong>${escapeHtml(node.name)}</strong><br>${escapeHtml(node.summary || "点击查看地点信息")}`)
    .nodeVisibility((node) => node.visible !== false)
    .nodeVal((node) => node.kind === "actor" ? 12 : node.active ? 8 : 4.5 + Math.min(3, Number(node.importance || 0) * 2))
    .nodeResolution(20)
    .nodeOpacity(1)
    .nodeColor((node) => node.kind === "actor" ? semanticPalette.current : node.active ? semanticPalette.current : node.focused === false ? "#8b97d4" : semanticPalette.place)
    .linkLabel((link) => escapeHtml(link.label || "连接"))
    .linkVisibility((link) => link.visible !== false)
    .linkColor((link) => link.current ? semanticPalette.current : link.kind === "journey" ? (semanticPalette[link.transport] || semanticPalette.road) : link.kind === "topology" ? "#718096" : semanticPalette.place)
    .linkWidth((link) => link.current ? 2.5 : link.kind === "journey" ? 1.2 : 0.55)
    .linkOpacity(0.76)
    .linkDirectionalArrowLength((link) => link.kind === "journey" ? 2.4 : 0)
    .linkDirectionalArrowRelPos(0.76)
    .linkDirectionalArrowColor((link) => link.current ? semanticPalette.current : (semanticPalette[link.transport] || semanticPalette.place))
    .linkDirectionalParticles((link) => link.current ? 1 : 0)
    .linkDirectionalParticleWidth(1.6)
    .linkDirectionalParticleColor(() => semanticPalette.current)
    .linkDirectionalParticleSpeed(0.012)
    .cooldownTicks(0)
    .onNodeHover((node) => {
      host.style.cursor = node && node.kind !== "actor" ? "pointer" : "grab";
    })
    .onNodeClick((node) => {
      if (node.kind === "actor") return;
      const step = journey.findIndex((event) => Number(event.location_entity_id) === node.locationId);
      if (step >= 0) setMapStep(step);
      else renderMapLocationDetails(node.locationId);
    });
  graph.enableNodeDrag?.(false);
  const controls = graph.controls();
  controls.enableRotate = true;
  controls.enablePan = true;
  controls.enableZoom = true;
  controls.enableDamping = true;
  controls.dampingFactor = 0.1;
  controls.rotateSpeed = 0.46;
  controls.zoomSpeed = 0.62;
  controls.panSpeed = 0.56;
  controls.minDistance = 70;
  controls.maxDistance = 3600;
  const resize = () => {
    graph.width(Math.max(320, host.clientWidth)).height(Math.max(500, Math.min(690, Math.round(window.innerHeight * 0.64))));
  };
  resize();
  state.mapGraphResizeObserver = new ResizeObserver(resize);
  state.mapGraphResizeObserver.observe(host);
  const cameraFocus = (node, animate = true) => {
    if (!node) return;
    graph.cameraPosition({ x: node.x + 190, y: node.y - 125, z: node.z + 260 }, { x: node.x, y: node.y, z: node.z }, animate ? 520 : 0);
  };
  state.mapViewportController = {
    focus(point, force = false, resetScale = false) {
      const node = nodes.find((item) => item.kind !== "actor" && item.mapPoint === point);
      if (node) cameraFocus(node, true);
    },
    focusGroup() {
      graph.zoomToFit(480, 76, (node) => node.kind !== "actor" && node.focused !== false);
    },
    fitAll() {
      graph.zoomToFit(520, 78, (node) => node.kind !== "actor");
    },
  };
  const zoomCamera = (factor) => {
    const camera = graph.camera();
    const target = controls.target || { x: 0, y: 0, z: 0 };
    graph.cameraPosition({
      x: target.x + (camera.position.x - target.x) * factor,
      y: target.y + (camera.position.y - target.y) * factor,
      z: target.z + (camera.position.z - target.z) * factor,
    }, target, 180);
  };
  $("#map-zoom-in")?.addEventListener("click", () => zoomCamera(0.82));
  $("#map-zoom-out")?.addEventListener("click", () => zoomCamera(1.18));
  $("#map-zoom-reset")?.addEventListener("click", () => graph.zoomToFit(520, 78, (node) => node.kind !== "actor"));
  let lastLabelDraw = 0;
  const drawLabels = (time) => {
    if (state.mapGraph !== graph) return;
    if (time - lastLabelDraw > 55) {
      renderMap3DLabels(graph, nodes);
      lastLabelDraw = time;
    }
    state.mapLabelFrame = requestAnimationFrame(drawLabels);
  };
  state.mapLabelFrame = requestAnimationFrame(drawLabels);
  setTimeout(() => {
    if (state.mapGraph !== graph) return;
    graph.zoomToFit(0, 78, (node) => node.kind !== "actor");
    if (currentNode) cameraFocus(currentNode, false);
    refresh();
  }, 80);
}

function renderMap3DLabels(graph, nodes) {
  const layer = $("#map-3d-labels");
  const host = $("#map-3d");
  if (!layer || !host) return;
  const candidates = nodes
    .filter((node) => node.kind !== "actor" && node.visible !== false && node.focused !== false && Number.isFinite(node.x) && Number.isFinite(node.y))
    .sort((left, right) => Number(right.active) - Number(left.active) || Number(right.importance || 0) - Number(left.importance || 0))
    .slice(0, 28);
  const occupied = [];
  const labels = [];
  for (const node of candidates) {
    const point = graph.graph2ScreenCoords(node.x, node.y, node.z || 0);
    if (!point || point.x < 12 || point.y < 12 || point.x > host.clientWidth - 12 || point.y > host.clientHeight - 12) continue;
    const label = mapDisplayName(node.name);
    const width = Math.max(52, [...label].length * 13 + 18);
    const placements = [[0, 30], [0, -30], [width / 2 + 18, 0], [-width / 2 - 18, 0]];
    let chosen = null;
    for (const [dx, dy] of placements) {
      const box = { left: point.x + dx - width / 2, right: point.x + dx + width / 2, top: point.y + dy - 13, bottom: point.y + dy + 13 };
      const outside = box.left < 6 || box.right > host.clientWidth - 6 || box.top < 6 || box.bottom > host.clientHeight - 6;
      const collides = occupied.some((placed) => !(box.right < placed.left || box.left > placed.right || box.bottom < placed.top || box.top > placed.bottom));
      if (!outside && (!collides || node.active)) { chosen = { x: point.x + dx, y: point.y + dy, box }; break; }
    }
    if (!chosen) continue;
    occupied.push(chosen.box);
    labels.push(`<span class="map-3d-label${node.active ? " active" : ""}" style="left:${chosen.x}px;top:${chosen.y}px">${escapeHtml(label)}</span>`);
  }
  layer.innerHTML = labels.join("");
}

function renderMapLocationDetails(locationId) {
  const panel = $("#map-location-panel");
  if (!panel || !state.overview) return;
  const location = state.overview.entities.find((item) => Number(item.id) === Number(locationId));
  if (!location) {
    panel.innerHTML = emptyState("地点信息待确认", "这个剧情步骤暂时没有可核验地点。");
    return;
  }
  const journey = storyMapSteps();
  const events = journey.filter((item) => Number(item.location_entity_id) === Number(locationId));
  const geography = (state.overview.geography_relations || []).filter((item) =>
    Number(item.source_entity_id) === Number(locationId) || Number(item.target_entity_id) === Number(locationId)
  );
  const routes = (state.overview.routes || []).filter((item) =>
    (Number(item.from_id) === Number(locationId) || Number(item.to_id) === Number(locationId))
      && (item.from_id === null || Number(item.from_id) !== Number(item.to_id))
  );
  const relationItems = geography.map((item) => {
    const outgoing = Number(item.source_entity_id) === Number(locationId);
    const otherName = outgoing ? item.target_name : item.source_name;
    const direction = outgoing ? geographyLabels[item.relative_position] : `参照 ${geographyLabels[item.relative_position] || item.relative_position}`;
    return `<li><strong>${escapeHtml(direction || item.relative_position)}</strong> ${escapeHtml(otherName)}<span>${escapeHtml(item.summary)}</span></li>`;
  }).join("");
  const routeItems = routes.map((item) => `<li><strong>${escapeHtml(item.from_name || "起点未知")} → ${escapeHtml(item.to_name || "终点未知")}</strong><span>${escapeHtml(transportLabels[item.transport] || item.transport || "方式未说明")} · ${escapeHtml(item.summary)}</span></li>`).join("");
  const eventItems = events.map((item) => `<button class="map-location-event" data-event="${item.id}" type="button"><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.temporal_value || "时间未知")} · ${escapeHtml(chapterForSegment(item.first_segment))}</span><small>${escapeHtml(eventNarrativeText(item))}</small></button>`).join("");
  panel.innerHTML = `<header><span class="eyebrow">地图节点</span><h3>${escapeHtml(location.name)}</h3><p>${escapeHtml(location.summary || "原文已识别这个地点，详细说明仍待补充。")}</p></header><div class="map-location-columns"><section><h4>此处发生的剧情</h4>${eventItems || '<p class="muted-copy">主线人物尚未在这里发生已收录事件。</p>'}</section><section><h4>方位与路线</h4>${relationItems || routeItems ? `<ul>${relationItems}${routeItems}</ul>` : '<p class="muted-copy">原文没有提供可验证的方位或通行关系。</p>'}</section></div>`;
  $$(".map-location-event").forEach((button) => button.addEventListener("click", () => {
    const step = journey.findIndex((item) => Number(item.id) === Number(button.dataset.event));
    if (step >= 0) setMapStep(step);
  }));
}

// 地图使用视窗坐标完成滚轮缩放和空白处拖动，节点点击仍然用于跳转剧情。
function bindMapViewport() {
  const svg = $(".map-svg");
  if (!svg) return;
  const bounds = state.mapBounds || { minX: 0, maxX: 900, minY: 0, maxY: 470 };
  const currentEvent = storyMapSteps()[state.mapStep];
  const initialPoint = state.mapPoints?.get(Number(currentEvent?.location_entity_id)) || [...(state.mapPoints?.values() || [])][0] || { x: 450, y: 235 };
  const defaultView = { x: initialPoint.x - 450, y: initialPoint.y - 235, width: 900, height: 470 };
  const view = state.mapViewport ? { ...state.mapViewport } : { ...defaultView };
  const apply = () => {
    const clampAxis = (value, size, minimum, maximum) => maximum - minimum <= size
      ? (minimum + maximum - size) / 2
      : Math.max(minimum, Math.min(maximum - size, value));
    view.x = clampAxis(view.x, view.width, bounds.minX, bounds.maxX);
    view.y = clampAxis(view.y, view.height, bounds.minY, bounds.maxY);
    svg.setAttribute("viewBox", `${view.x} ${view.y} ${view.width} ${view.height}`);
    state.mapViewport = { ...view };
  };
  const zoom = (factor, clientX = null, clientY = null) => {
    const rect = svg.getBoundingClientRect();
    const px = clientX === null ? 0.5 : Math.max(0, Math.min(1, (clientX - rect.left) / Math.max(1, rect.width)));
    const py = clientY === null ? 0.5 : Math.max(0, Math.min(1, (clientY - rect.top) / Math.max(1, rect.height)));
    const maxWidth = Math.max(1120, bounds.maxX - bounds.minX);
    const nextWidth = Math.max(430, Math.min(maxWidth, view.width * factor));
    const nextHeight = nextWidth * 470 / 900;
    view.x += (view.width - nextWidth) * px;
    view.y += (view.height - nextHeight) * py;
    view.width = nextWidth;
    view.height = nextHeight;
    apply();
  };
  svg.addEventListener("wheel", (event) => {
    event.preventDefault();
    zoom(event.deltaY > 0 ? 1.1 : 0.9, event.clientX, event.clientY);
  }, { passive: false });

  let dragging = null;
  svg.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.closest(".map-node, .journey-route")) return;
    dragging = { x: event.clientX, y: event.clientY, viewX: view.x, viewY: view.y };
    svg.classList.add("dragging");
    svg.setPointerCapture(event.pointerId);
  });
  svg.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    const rect = svg.getBoundingClientRect();
    view.x = dragging.viewX - (event.clientX - dragging.x) * view.width / Math.max(1, rect.width);
    view.y = dragging.viewY - (event.clientY - dragging.y) * view.height / Math.max(1, rect.height);
    apply();
  });
  const stopDragging = (event) => {
    if (!dragging) return;
    dragging = null;
    svg.classList.remove("dragging");
    if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
  };
  svg.addEventListener("pointerup", stopDragging);
  svg.addEventListener("pointercancel", stopDragging);
  $("#map-zoom-in")?.addEventListener("click", () => zoom(0.82));
  $("#map-zoom-out")?.addEventListener("click", () => zoom(1.18));
  $("#map-zoom-reset")?.addEventListener("click", () => {
    Object.assign(view, defaultView);
    apply();
  });
  state.mapViewportController = {
    focus(point, force = false, resetScale = false) {
      if (resetScale) {
        view.width = 900;
        view.height = 470;
      }
      const marginX = view.width * 0.2;
      const marginY = view.height * 0.2;
      const outside = point.x < view.x + marginX || point.x > view.x + view.width - marginX
        || point.y < view.y + marginY || point.y > view.y + view.height - marginY;
      if (!force && !outside) return;
      view.x = point.x - view.width / 2;
      view.y = point.y - view.height / 2;
      apply();
    },
    focusGroup(points, anchor = null) {
      const available = points.filter((point) => point && Number.isFinite(point.x) && Number.isFinite(point.y));
      if (!available.length) {
        if (anchor) this.focus(anchor, true);
        return;
      }
      const minX = Math.min(...available.map((point) => point.x));
      const maxX = Math.max(...available.map((point) => point.x));
      const minY = Math.min(...available.map((point) => point.y));
      const maxY = Math.max(...available.map((point) => point.y));
      const ratio = 900 / 470;
      const requiredWidth = Math.max(620, maxX - minX + 240, (maxY - minY + 180) * ratio);
      view.width = requiredWidth;
      view.height = requiredWidth / ratio;
      view.x = (minX + maxX - view.width) / 2;
      view.y = (minY + maxY - view.height) / 2;
      apply();
    },
    fitAll() {
      const width = Math.max(900, bounds.maxX - bounds.minX);
      const height = Math.max(470, bounds.maxY - bounds.minY);
      const ratio = 900 / 470;
      view.width = Math.max(width, height * ratio);
      view.height = view.width / ratio;
      view.x = (bounds.minX + bounds.maxX - view.width) / 2;
      view.y = (bounds.minY + bounds.maxY - view.height) / 2;
      apply();
    },
  };
  apply();
}

function syncMap3DStep(event, visibleLocationIds, step, animate) {
  const graph = state.mapGraph;
  const nodes = state.map3DNodes;
  const links = state.map3DLinks;
  const actor = state.map3DActor;
  if (state.mapMode !== "3d" || !graph || !nodes || !links || !actor) return;
  const currentLocationId = event.location_entity_id === null ? null : Number(event.location_entity_id);
  nodes.forEach((node) => {
    if (node.kind === "actor") return;
    node.active = currentLocationId !== null && node.locationId === currentLocationId;
    node.visible = true;
    node.focused = state.mapShowFullRoute || visibleLocationIds.has(node.locationId);
  });
  links.forEach((link) => {
    const endpointsVisible = visibleLocationIds.has(link.sourceLocationId) && visibleLocationIds.has(link.targetLocationId);
    const nearStep = link.kind !== "journey" || (Number(link.step) >= step - 5 && Number(link.step) <= step + 3);
    link.visible = link.kind === "topology"
      ? state.mapShowFullRoute
      : state.mapShowFullRoute || (endpointsVisible && nearStep);
    link.current = link.kind === "journey" && Number(link.step) === step;
  });
  const target = currentLocationId === null
    ? null
    : nodes.find((node) => node.kind !== "actor" && node.locationId === currentLocationId);
  if (!target) {
    cancelAnimationFrame(state.mapAnimationFrame);
    state.mapAnimationFrame = null;
    actor.visible = false;
    graph.refresh();
    return;
  }
  actor.visible = true;
  animateMapMarker3D({ x: target.x, y: target.y, z: target.z + 34 }, animate);
  if (state.mapShowFullRoute) state.mapViewportController?.focus(target.mapPoint);
  else state.mapViewportController?.focusGroup();
  graph.refresh();
}

function animateMapMarker3D(target, animate) {
  const graph = state.mapGraph;
  const actor = state.map3DActor;
  if (!graph || !actor) return;
  const start = state.mapMarkerPoint3D || { x: actor.x, y: actor.y, z: actor.z } || target;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  cancelAnimationFrame(state.mapAnimationFrame);
  const applyPoint = (point) => {
    actor.x = point.x;
    actor.y = point.y;
    actor.z = point.z;
    actor.fx = point.x;
    actor.fy = point.y;
    actor.fz = point.z;
    state.mapMarkerPoint3D = point;
    graph.refresh();
  };
  if (!animate || reduceMotion || (start.x === target.x && start.y === target.y && start.z === target.z)) {
    applyPoint(target);
    return;
  }
  const started = performance.now();
  const duration = 720;
  const frame = (now) => {
    if (state.mapGraph !== graph || state.map3DActor !== actor) return;
    const progress = Math.min(1, (now - started) / duration);
    const eased = 1 - Math.pow(1 - progress, 3);
    const point = {
      x: start.x + (target.x - start.x) * eased,
      y: start.y + (target.y - start.y) * eased,
      z: start.z + (target.z - start.z) * eased,
    };
    applyPoint(progress < 1 ? point : target);
    if (progress < 1) state.mapAnimationFrame = requestAnimationFrame(frame);
    else state.mapAnimationFrame = null;
  };
  state.mapAnimationFrame = requestAnimationFrame(frame);
}

function setMapStep(nextStep, animate = true) {
  const journey = storyMapSteps();
  if (!journey.length || state.view !== "map") return;
  const step = Math.max(0, Math.min(Number(nextStep), journey.length - 1));
  const event = journey[step];
  // 只有当前事件明确给出地点时才显示人物标记。地点未知时保留上一个真实坐标作为
  // 下一次移动的起点，但隐藏标记，避免把上一地点冒充为当前地点。
  const target = event.location_entity_id !== null ? state.mapPoints?.get(event.location_entity_id) : null;
  state.mapStep = step;
  const focusEvents = journey.slice(Math.max(0, step - 5), Math.min(journey.length, step + 4));
  const visibleLocationIds = new Set(
    focusEvents.filter((item) => item.location_entity_id !== null).map((item) => Number(item.location_entity_id)),
  );
  // 连续数个事件都缺少地点时，保留前后最近的已知地点作为地图参照，但人物标记仍然隐藏。
  // 这样可以说明故事从哪里来、可能往哪里去，同时不会把参照地点误报成当前地点。
  if (!visibleLocationIds.size) {
    const previousLocated = journey.slice(0, step).reverse().find((item) => item.location_entity_id !== null);
    const nextLocated = journey.slice(step + 1).find((item) => item.location_entity_id !== null);
    if (previousLocated) visibleLocationIds.add(Number(previousLocated.location_entity_id));
    if (nextLocated) visibleLocationIds.add(Number(nextLocated.location_entity_id));
  }
  const nearbyRelations = [...(state.overview.geography_relations || [])]
    .sort((left, right) => Number(right.confidence || 0) - Number(left.confidence || 0));
  for (const relation of nearbyRelations) {
    if (visibleLocationIds.size >= 14) break;
    const sourceId = Number(relation.source_entity_id);
    const targetId = Number(relation.target_entity_id);
    if (visibleLocationIds.has(sourceId) || visibleLocationIds.has(targetId)) {
      visibleLocationIds.add(sourceId);
      visibleLocationIds.add(targetId);
    }
  }
  $$(".journey-route").forEach((route) => {
    const routeStep = Number(route.dataset.step);
    const outsideFocus = routeStep < step - 5 || routeStep > step + 3;
    route.classList.toggle("completed", routeStep < step);
    route.classList.toggle("current", routeStep === step);
    route.classList.toggle("pending", routeStep > step);
    route.classList.toggle("far", !state.mapShowFullRoute && outsideFocus);
  });
  $$(".map-node").forEach((node) => {
    const locationId = Number(node.dataset.location);
    node.classList.toggle("active", event.location_entity_id !== null && locationId === Number(event.location_entity_id));
    node.classList.toggle("far", !state.mapShowFullRoute && !visibleLocationIds.has(locationId));
  });
  $$(".geography-relation").forEach((relation) => {
    const endpointsVisible = visibleLocationIds.has(Number(relation.dataset.source)) && visibleLocationIds.has(Number(relation.dataset.target));
    relation.classList.toggle("far", !state.mapShowFullRoute && !endpointsVisible);
  });
  $$(".route-topology").forEach((route) => {
    const endpointsVisible = visibleLocationIds.has(Number(route.dataset.source)) && visibleLocationIds.has(Number(route.dataset.target));
    route.classList.toggle("far", !state.mapShowFullRoute || !endpointsVisible);
  });
  syncMap3DStep(event, visibleLocationIds, step, animate);
  if (target) {
    const focusPoints = [...visibleLocationIds].map((locationId) => state.mapPoints?.get(locationId)).filter(Boolean);
    if (state.mapMode === "2d") {
      if (state.mapShowFullRoute) state.mapViewportController?.focus(target);
      else state.mapViewportController?.focusGroup(focusPoints, target);
      animateMapMarker(target, animate);
    }
  } else if (state.mapMode === "2d") {
    cancelAnimationFrame(state.mapAnimationFrame);
    state.mapAnimationFrame = null;
    $("#journey-avatar")?.setAttribute("hidden", "");
  }
  if (event.location_entity_id !== null && event.location_entity_id !== undefined) {
    state.activeLocation = Number(event.location_entity_id);
    renderMapLocationDetails(state.activeLocation);
  } else {
    state.activeLocation = null;
    renderMapLocationDetails(null);
  }
  $("#map-step-slider").value = String(step);
  $("#map-step-count").textContent = `${step + 1}/${journey.length}`;
  $("#map-prev").disabled = step === 0;
  $("#map-next").disabled = step === journey.length - 1;
  $("#map-next").textContent = step === journey.length - 1 ? "已到末步" : "下一步";
  const participants = [...new Set(event.participants.map((person) => person.name))].join("、") || "未标明";
  const history = event.location_entity_id === null
    ? [event]
    : journey.filter((item) => Number(item.location_entity_id) === Number(event.location_entity_id));
  const leg = (state.overview.routes || []).find((route) => Number(route.event_id) === Number(event.id));
  const displayedLocation = event.location_name || "地点待确认";
  const pathStatus = event.location_entity_id === null
    ? "原文没有确认当前地点，人物标记暂时隐藏"
    : leg?.gap_status === "unknown_path" ? "原文路径有缺口，节点仍完整保留" : "路线连续";
  $("#map-event-card").innerHTML = `<span class="eyebrow">编年第 ${step + 1} 步 · ${escapeHtml(chapterForSegment(event.first_segment))}</span><h3>${escapeHtml(event.title)}</h3><p>${escapeHtml(eventNarrativeText(event))}</p><div class="map-event-facts"><span><b>故事时间</b>${escapeHtml(event.temporal_value || "时间未知")}</span><span><b>当前地点</b>${escapeHtml(displayedLocation)}</span><span><b>交通方式</b>${escapeHtml(transportLabels[leg?.transport || event.transport] || leg?.transport || event.transport || "未说明")}</span><span><b>路径状态</b>${escapeHtml(pathStatus)}</span><span><b>在场人物</b>${escapeHtml(participants)}</span></div><button id="map-evidence" class="button button-quiet full" type="button">打开这一步的原文</button><div class="location-history"><strong>${event.location_entity_id === null ? "当前故事步骤" : `此地共发生 ${history.length} 个编年事件`}</strong>${history.map((item) => `<button class="map-history-step" data-event="${item.id}" type="button">${escapeHtml(item.temporal_value || "时间未知")} · ${escapeHtml(item.title)}</button>`).join("")}</div>`;
  $("#map-evidence")?.addEventListener("click", () => openEventSource(event));
  $$(".map-history-step").forEach((button) => button.addEventListener("click", () => {
    const historyStep = journey.findIndex((item) => Number(item.id) === Number(button.dataset.event));
    if (historyStep >= 0) setMapStep(historyStep);
  }));
  if (step === journey.length - 1 && state.mapPlaybackState === "playing") {
    stopMapPlayback(false);
    state.mapPlaybackState = "complete";
    $("#map-play").textContent = "重新播放";
  }
}

async function openEventSource(event) {
  // 地图只打开一个原文对话框，不再创建独立的右侧详情栏。
  try {
    const evidence = await api(`/api/evidence/event/${event.id}`);
    const source = evidence[0];
    if (!source) {
      toast("这一步还没有可打开的原文证据。", true);
      return;
    }
    await openSource(Number(source.segment_id), source.quote || "");
  } catch (error) {
    toast(error.message, true);
  }
}

function animateMapMarker(target, animate) {
  const marker = $("#journey-avatar");
  if (!marker) return;
  marker.removeAttribute("hidden");
  const start = state.mapMarkerPoint || target;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  cancelAnimationFrame(state.mapAnimationFrame);
  if (!animate || reduceMotion || (start.x === target.x && start.y === target.y)) {
    marker.setAttribute("transform", `translate(${target.x} ${target.y})`);
    state.mapMarkerPoint = target;
    return;
  }
  const started = performance.now();
  const duration = 720;
  const frame = (now) => {
    const progress = Math.min(1, (now - started) / duration);
    const eased = 1 - Math.pow(1 - progress, 3);
    const x = start.x + (target.x - start.x) * eased;
    const y = start.y + (target.y - start.y) * eased;
    marker.setAttribute("transform", `translate(${x} ${y})`);
    // 逐帧保存屏幕上的实际位置。快速切换步骤中断当前动画时，下一段会从这一帧继续走。
    state.mapMarkerPoint = progress < 1 ? { x, y } : target;
    if (progress < 1) state.mapAnimationFrame = requestAnimationFrame(frame);
    else state.mapAnimationFrame = null;
  };
  state.mapAnimationFrame = requestAnimationFrame(frame);
}

function toggleMapPlayback() {
  const journey = storyMapSteps();
  if (!journey.length) return;
  if (state.mapPlaybackState === "playing") {
    stopMapPlayback(false);
    state.mapPlaybackState = "paused";
    $("#map-play").textContent = "继续播放";
    return;
  }
  if (state.mapStep >= journey.length - 1) setMapStep(0, false);
  state.mapPlaybackState = "playing";
  $("#map-play").textContent = "暂停播放";
  const runId = ++state.mapPlaybackRunId;
  const scheduleNext = () => {
    clearTimeout(state.mapTimer);
    state.mapTimer = setTimeout(() => {
      if (state.mapPlaybackState !== "playing" || state.mapPlaybackRunId !== runId || state.view !== "map") return;
      setMapStep(state.mapStep + 1);
      if (state.mapStep < journey.length - 1 && state.mapPlaybackState === "playing") scheduleNext();
    }, 1550);
  };
  scheduleNext();
}

function bindProtagonistPicker() {
  $("#protagonist-select")?.addEventListener("change", async (event) => {
    const automatic = event.target.value === "auto";
    try {
      await api(`/api/books/${state.bookId}/settings`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ protagonist_entity_id: automatic ? null : Number(event.target.value), auto_protagonist: automatic }),
      });
      await loadOverview(Number($("#progress-slider").value));
    } catch (error) {
      toast(error.message, true);
    }
  });
}

function renderWorld(query = "", category = "all") {
  const allNotes = state.overview.world_notes || [];
  const categories = ["power", "faction", "background", "rule", "geography", "culture", "other"];
  const notes = allNotes.filter((note) => {
    const searchable = `${note.title} ${note.summary} ${categoryLabels[note.category] || note.category}`.toLowerCase();
    return (category === "all" || note.category === category) && searchable.includes(query.toLowerCase());
  });
  const options = ['<option value="all">全部分类</option>', ...categories.map((item) => `<option value="${item}" ${item === category ? "selected" : ""}>${escapeHtml(categoryLabels[item] || item)}</option>`)].join("");
  const cards = notes.map((note) => `<article class="knowledge-card">
    <div class="knowledge-card-meta"><span class="category">${escapeHtml(categoryLabels[note.category] || note.category)}${note.created_by === "synthesis" ? " · 全书整理" : note.created_by === "human" ? " · 人工补充" : " · 原文事实"}</span><span class="confidence">${note.created_by === "human" && !note.evidence_count ? "人工内容" : `${Math.round(note.confidence * 100)}% · ${note.evidence_count} 证据`}</span></div>
    <h3>${escapeHtml(note.title)}</h3><p>${escapeHtml(note.summary)}</p>
    <div class="knowledge-card-actions"><button class="button button-quiet target-button" data-type="world_note" data-id="${note.id}" type="button">查看、编辑或二次生成</button><button class="button button-danger world-archive" data-id="${note.id}" type="button">归档</button></div>
  </article>`).join("");
  const empty = emptyState(allNotes.length ? "没有匹配的世界信息" : "还没有世界信息", allNotes.length ? "更换关键词或分类。" : "可以从原文分析生成，也可以人工创建一条明确标记的补充内容。");
  $("#view-panel").innerHTML = `${panelHead("世界信息", "可搜索、分类、创建、编辑、二次生成、归档和恢复；原文事实与人工补充始终分开标记。")}
    <div class="world-toolbar"><input id="world-search" class="search-input" type="search" value="${escapeHtml(query)}" placeholder="搜索规则、势力、背景或地理" aria-label="搜索世界信息"><select id="world-category" class="search-input" aria-label="筛选世界信息分类">${options}</select><button id="world-archived" class="button button-quiet" type="button">归档记录</button></div>
    <details class="world-create"><summary>创建世界信息</summary><div class="world-create-form"><label>分类<select id="world-create-category">${categories.map((item) => `<option value="${item}">${escapeHtml(categoryLabels[item] || item)}</option>`).join("")}</select></label><label>标题<input id="world-create-title" maxlength="160" placeholder="例如：灵力修炼层级"></label><label>说明<textarea id="world-create-summary" maxlength="5000" rows="4" placeholder="写清规则、条件、限制或影响；人工内容会明确标记。"></textarea></label><button id="world-create-submit" class="button button-primary" type="button">创建并继续编辑</button></div></details>
    <div id="world-archive-list"></div>${notes.length ? `<div class="card-grid">${cards}</div>` : empty}`;
  $("#world-search").addEventListener("input", (event) => renderWorld(event.target.value, $("#world-category").value));
  $("#world-category").addEventListener("change", (event) => renderWorld($("#world-search").value, event.target.value));
  $("#world-create-submit").addEventListener("click", async () => {
    const title = $("#world-create-title").value.trim();
    const summary = $("#world-create-summary").value.trim();
    if (!title || !summary) {
      toast("请填写标题和说明。", true);
      return;
    }
    try {
      const created = await api(`/api/books/${state.bookId}/world-notes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category: $("#world-create-category").value, title, summary }),
      });
      toast("世界信息已创建，并标记为人工补充。");
      await loadOverview(Number($("#progress-slider").value), true);
      openInspector("world_note", Number(created.id));
    } catch (error) {
      toast(error.message, true);
    }
  });
  $$(".world-archive").forEach((button) => button.addEventListener("click", async () => {
    if (!window.confirm("归档后不会出现在世界信息中，仍可从归档记录恢复。继续吗？")) return;
    try {
      await api(`/api/world-notes/${button.dataset.id}`, { method: "DELETE" });
      toast("世界信息已归档，可随时恢复。");
      await loadOverview(Number($("#progress-slider").value), true);
    } catch (error) {
      toast(error.message, true);
    }
  }));
  $("#world-archived").addEventListener("click", loadArchivedWorldNotes);
  bindTargets();
}

async function loadArchivedWorldNotes() {
  const host = $("#world-archive-list");
  if (!host) return;
  host.innerHTML = '<div class="loading compact">正在读取归档记录…</div>';
  try {
    const notes = await api(`/api/books/${state.bookId}/world-notes/archived`);
    host.innerHTML = notes.length ? `<div class="world-archive-panel"><h3>已归档世界信息</h3>${notes.map((note) => `<div><span><strong>${escapeHtml(note.title)}</strong><small>${escapeHtml(categoryLabels[note.category] || note.category)} · ${escapeHtml(note.summary)}</small></span><button class="button button-quiet world-restore" data-id="${note.id}" type="button">恢复</button></div>`).join("")}</div>` : emptyState("没有归档记录", "归档的世界信息会出现在这里。");
    $$(".world-restore").forEach((button) => button.addEventListener("click", async () => {
      try {
        await api(`/api/world-notes/${button.dataset.id}/restore`, { method: "POST" });
        toast("世界信息已经恢复。");
        await loadOverview(Number($("#progress-slider").value), true);
      } catch (error) {
        toast(error.message, true);
      }
    }));
  } catch (error) {
    host.innerHTML = emptyState("归档记录读取失败", error.message);
  }
}

function renderDatabase(query = "", category = "all") {
  const facets = state.knowledgeFacets || { categories: [], concept_count: 0, evidence_link_count: 0, needs_classification: 0 };
  const concepts = (state.concepts || []).filter((concept) => {
    if (concept.scheme === "system") return false;
    const haystack = `${concept.preferred_label} ${concept.description} ${(concept.aliases || []).join(" ")}`.toLowerCase();
    return (category === "all" || concept.category === category) && haystack.includes(query.toLowerCase());
  });
  const facetButtons = [`<button class="knowledge-facet${category === "all" ? " active" : ""}" data-category="all" type="button"><span>全部知识</span><strong>${Number(facets.concept_count || 0)}</strong></button>`, ...(facets.categories || []).map((item) => `<button class="knowledge-facet${category === item.key ? " active" : ""}" data-category="${escapeHtml(item.key)}" type="button"><span>${escapeHtml(item.label || categoryLabels[item.key] || item.key)}</span><strong>${Number(item.count || 0)}</strong></button>`)].join("");
  const conceptRows = concepts.length ? `<div class="concept-list">${concepts.map((concept) => `<button class="concept-row" data-concept="${concept.id}" type="button"><span><strong>${escapeHtml(concept.preferred_label)}</strong><span>${escapeHtml(concept.description || "尚未填写读者说明")}${concept.aliases?.length ? ` · 别名：${escapeHtml(concept.aliases.join("、"))}` : ""}</span></span><small>${escapeHtml(categoryLabels[concept.category] || concept.category)} · ${Number(concept.claim_count || 0)} 条事实 · ${Number(concept.evidence_count || 0)} 条证据</small></button>`).join("")}</div>` : emptyState("没有匹配的知识概念", "更换关键词或分类，无法自动判断的内容会保留在待归类中。");
  const parentOptions = [`<option value="">不设置上位概念</option>`, ...(state.concepts || []).filter((item) => item.status === "active").map((item) => `<option value="${item.id}">${escapeHtml(item.preferred_label)}</option>`)].join("");
  $("#view-panel").innerHTML = panelHead("知识库", "原子事实、概念分类和读者说明分层保存；原文、人工内容与外部资料不会混成一种证据。") + `<div class="knowledge-workspace"><aside class="knowledge-sidebar"><h3>分类</h3>${facetButtons}<details class="world-create"><summary>创建概念或文件夹</summary><div class="world-create-form"><label>分类<input id="concept-create-category" maxlength="80" value="term" placeholder="例如：skill"></label><label>名称<input id="concept-create-label" maxlength="160" placeholder="稳定、便于检索的名称"></label><label>上位概念<select id="concept-create-parent">${parentOptions}</select></label><label>别名<input id="concept-create-aliases" maxlength="500" placeholder="使用逗号分隔"></label><label>说明<textarea id="concept-create-description" rows="4" maxlength="5000"></textarea></label><button id="concept-create-submit" class="button button-primary" type="button">创建概念</button></div></details></aside><section class="knowledge-main"><div class="database-toolbar"><input id="entry-search" class="search-input" value="${escapeHtml(query)}" placeholder="搜索名称、别名、说明或证据" aria-label="搜索知识库"></div><div class="knowledge-summary-grid"><article><strong>${Number(facets.concept_count || 0)}</strong><span>知识概念</span></article><article><strong>${Number(facets.evidence_link_count || 0)}</strong><span>证据连接</span></article><article><strong>${Number(facets.needs_classification || 0)}</strong><span>待归类</span></article></div>${conceptRows}</section></div>`;
  $("#entry-search").addEventListener("input", (event) => renderDatabase(event.target.value, category));
  $$(".knowledge-facet").forEach((button) => button.addEventListener("click", () => renderDatabase(query, button.dataset.category)));
  $$(".concept-row").forEach((button) => button.addEventListener("click", () => openConceptDetails(Number(button.dataset.concept))));
  $("#concept-create-submit").addEventListener("click", async () => {
    const label = $("#concept-create-label").value.trim();
    const rawAliases = $("#concept-create-aliases").value.split(/[，,]/).map((item) => item.trim()).filter(Boolean);
    if (!label) return toast("请填写概念名称。", true);
    try {
      const created = await api(`/api/books/${state.bookId}/concepts`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          category: $("#concept-create-category").value.trim() || "other",
          preferred_label: label,
          description: $("#concept-create-description").value.trim(),
          aliases: rawAliases,
          parent_concept_id: Number($("#concept-create-parent").value) || null,
          scheme: "custom",
        }),
      });
      await loadOverview(Number($("#progress-slider").value), true);
      openConceptDetails(Number(created.id));
    } catch (error) { toast(error.message, true); }
  });
}

async function openConceptDetails(conceptId) {
  const concept = (state.concepts || []).find((item) => Number(item.id) === Number(conceptId));
  if (!concept) return;
  state.inspectorTarget = { type: "concept", id: Number(conceptId) };
  const requestSerial = ++state.inspectorRequestSerial;
  $("#inspector-title").textContent = concept.preferred_label;
  $("#inspector-body").innerHTML = '<div class="loading">正在读取原子事实…</div>';
  $("#inspector").classList.add("open");
  $(".app-shell").classList.add("inspector-open");
  $("#inspector").setAttribute("aria-hidden", "false");
  $("#inspector").removeAttribute("inert");
  $("#scrim").hidden = !window.matchMedia("(max-width: 1100px)").matches;
  try {
    const [claims, revisions] = await Promise.all([
      api(`/api/books/${state.bookId}/knowledge-claims?concept_id=${conceptId}`),
      api(`/api/books/${state.bookId}/knowledge-revisions?target_type=concept&target_id=${conceptId}&limit=20`),
    ]);
    if (requestSerial !== state.inspectorRequestSerial) return;
    const sourceOptions = state.overview.segments.map((segment) => `<option value="${segment.id}">${escapeHtml(segment.chapter_title)}</option>`).join("");
    const claimCards = claims.map((claim) => {
      const value = typeof claim.value === "string" ? claim.value : JSON.stringify(claim.value, null, 2);
      const originalAction = ["entity", "world_note", "entry"].includes(claim.subject_type) ? `<button class="button button-quiet knowledge-original" data-type="${escapeHtml(claim.subject_type)}" data-id="${claim.subject_id}" type="button">打开原记录</button>` : "";
      return `<article class="evidence-card knowledge-claim" data-claim="${claim.id}"><strong>${escapeHtml(claim.predicate)}</strong><p>${escapeHtml(value)}</p><small>${escapeHtml(claim.source_kind)} · ${escapeHtml(claim.status)} · ${Number(claim.evidence_count || 0)} 条证据</small><div class="review-actions">${originalAction}${claim.evidence_count ? `<button class="button button-quiet knowledge-claim-evidence" data-id="${claim.id}" type="button">查看证据</button>` : ""}<button class="button button-danger knowledge-claim-deprecate" data-id="${claim.id}" type="button">弃用</button></div><details class="record-editor"><summary>修改事实</summary><textarea class="knowledge-claim-value">${escapeHtml(value)}</textarea><select class="knowledge-claim-status"><option value="accepted" ${claim.status === "accepted" ? "selected" : ""}>正式</option><option value="parallel" ${claim.status === "parallel" ? "selected" : ""}>并列</option><option value="needs_resolution" ${claim.status === "needs_resolution" ? "selected" : ""}>待解决</option><option value="deprecated" ${claim.status === "deprecated" ? "selected" : ""}>弃用</option></select><button class="button button-primary knowledge-claim-save" data-id="${claim.id}" type="button">保存修改</button></details></article>`;
    }).join("") || '<p class="detail-summary">当前概念还没有原子事实。</p>';
    const revisionCards = revisions.length ? revisions.map((item) => `<li><strong>${escapeHtml(item.action)}</strong><span>${escapeHtml(item.created_at)}</span></li>`).join("") : "<li><span>还没有人工修改记录</span></li>";
    $("#inspector-body").innerHTML = `<p class="detail-summary">${escapeHtml(concept.description || "当前概念没有读者说明。")}</p><div class="detail-row"><span>分类</span><strong>${escapeHtml(categoryLabels[concept.category] || concept.category)}</strong></div><div class="detail-row"><span>别名</span><strong>${escapeHtml((concept.aliases || []).join("、") || "—")}</strong></div><div class="record-editor"><label>名称<input id="concept-edit-label" value="${escapeHtml(concept.preferred_label)}"></label><label>说明<textarea id="concept-edit-description">${escapeHtml(concept.description || "")}</textarea></label><label>别名<input id="concept-edit-aliases" value="${escapeHtml((concept.aliases || []).join("，"))}"></label><div class="review-actions"><button id="concept-save" class="button button-primary" type="button">保存概念</button><button id="concept-archive" class="button button-danger" type="button">归档概念</button></div></div><h3 class="evidence-title">原子事实</h3>${claimCards}<details class="record-editor"><summary>新增事实</summary><label>属性<input id="knowledge-claim-predicate" maxlength="120" placeholder="例如：使用条件"></label><label>值<textarea id="knowledge-claim-value"></textarea></label><label>来源<select id="knowledge-claim-source"><option value="human_note">人工说明</option><option value="original_text">原文事实</option><option value="external_fact">外部资料</option></select></label><label>原文章节<select id="knowledge-claim-segment">${sourceOptions}</select></label><label>逐字引文<textarea id="knowledge-claim-quote" maxlength="800"></textarea></label><button id="knowledge-claim-create" class="button button-primary" type="button">保存事实</button></details><details class="record-editor knowledge-history"><summary>修改记录 · ${revisions.length}</summary><ul>${revisionCards}</ul></details>`;
    $("#concept-save").addEventListener("click", async () => {
      try {
        await api(`/api/concepts/${conceptId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ preferred_label: $("#concept-edit-label").value.trim(), description: $("#concept-edit-description").value.trim(), aliases: $("#concept-edit-aliases").value.split(/[，,]/).map((item) => item.trim()).filter(Boolean) }) });
        closeInspector(); await loadOverview(Number($("#progress-slider").value), true); toast("概念已经保存。");
      } catch (error) { toast(error.message, true); }
    });
    $("#concept-archive").addEventListener("click", async () => {
      if (!window.confirm("归档概念后，事实和证据仍会保留。继续吗？")) return;
      try { await api(`/api/concepts/${conceptId}`, { method: "DELETE" }); closeInspector(); await loadOverview(Number($("#progress-slider").value), true); } catch (error) { toast(error.message, true); }
    });
    $("#knowledge-claim-create").addEventListener("click", async () => {
      const sourceKind = $("#knowledge-claim-source").value;
      const predicate = $("#knowledge-claim-predicate").value.trim();
      const value = $("#knowledge-claim-value").value.trim();
      if (!predicate || !value) return toast("请填写属性和值。", true);
      try {
        await api(`/api/books/${state.bookId}/knowledge-claims`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ concept_id: conceptId, predicate, value, source_kind: sourceKind, confidence: sourceKind === "original_text" ? 0.9 : 1, segment_id: sourceKind === "original_text" ? Number($("#knowledge-claim-segment").value) : null, evidence_quote: sourceKind === "original_text" ? $("#knowledge-claim-quote").value : "", qualifiers: {} }) });
        await loadOverview(Number($("#progress-slider").value), true); openConceptDetails(conceptId);
      } catch (error) { toast(error.message, true); }
    });
    $$(".knowledge-original").forEach((button) => button.addEventListener("click", () => openInspector(button.dataset.type, Number(button.dataset.id))));
    $$(".knowledge-claim-evidence").forEach((button) => button.addEventListener("click", async () => {
      const evidence = await api(`/api/evidence/knowledge_claim/${Number(button.dataset.id)}`);
      if (!evidence.length) return toast("这条事实当前没有原文证据。", true);
      openSource(Number(evidence[0].segment_id), evidence[0].quote);
    }));
    $$(".knowledge-claim-deprecate").forEach((button) => button.addEventListener("click", async () => {
      try {
        await api(`/api/knowledge-claims/${Number(button.dataset.id)}`, { method: "DELETE" });
        await loadOverview(Number($("#progress-slider").value), true);
        openConceptDetails(conceptId);
      } catch (error) { toast(error.message, true); }
    }));
    $$(".knowledge-claim-save").forEach((button) => button.addEventListener("click", async () => {
      const card = button.closest(".knowledge-claim");
      const raw = card.querySelector(".knowledge-claim-value").value.trim();
      let value = raw; try { value = JSON.parse(raw); } catch (_) { value = raw; }
      try {
        await api(`/api/knowledge-claims/${Number(button.dataset.id)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ value, status: card.querySelector(".knowledge-claim-status").value }) });
        await loadOverview(Number($("#progress-slider").value), true);
        openConceptDetails(conceptId);
      } catch (error) { toast(error.message, true); }
    }));
  } catch (error) { if (requestSerial === state.inspectorRequestSerial) $("#inspector-body").innerHTML = `<p class="detail-summary">${escapeHtml(error.message)}</p>`; }
}

const collaborationStatusLabels = {
  interpreted: "已经解释",
  confirmed: "已经确认",
  implementing: "正在实施",
  validating: "正在验收",
  released: "已经发布",
  rejected: "已经拒绝",
};

async function refreshControlPlane(silent = false) {
  if (!state.bookId) return;
  const requestedBookId = state.bookId;
  if (!silent) $("#view-panel").innerHTML = panelHead("协作控制", "正在读取合同、提示词、规则和运行记录。") + '<div class="loading">正在建立透明执行视图…</div>';
  try {
    const payload = await api(`/api/books/${requestedBookId}/control-plane`);
    if (requestedBookId !== state.bookId) return;
    state.controlPlane = payload;
    state.controlPlaneBookId = requestedBookId;
    if (state.view === "collaboration") renderCollaboration();
  } catch (error) {
    $("#view-panel").innerHTML = panelHead("协作控制", "读取失败") + emptyState("无法读取协作控制台", error.message);
  }
}

function promptDetailPanel() {
  const detail = state.promptDetail;
  if (!detail) return "";
  const core = detail.layers?.find((layer) => layer.key === "core")?.text || "";
  const task = detail.layers?.find((layer) => layer.key === "task")?.text || "";
  const isExtraction = detail.task_key === "extraction";
  const segmentOptions = state.overview.segments.map((segment) => `<option value="${segment.id}">${escapeHtml(chapterForSegment(segment.ordinal))}</option>`).join("");
  const providerOptions = state.providers.filter((item) => item.available && item.id !== "auto").map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.label)}</option>`).join("");
  const runtimeControls = isExtraction ? `<div class="prompt-runtime-controls"><label>完整调用预览章节<select id="prompt-runtime-segment">${state.overview.segments.map((segment) => `<option value="${segment.id}" ${Number(detail.runtime_segment_id) === Number(segment.id) ? "selected" : ""}>${escapeHtml(chapterForSegment(segment.ordinal))}</option>`).join("")}</select></label><button id="prompt-runtime-preview" class="button button-quiet" type="button">拼装这一章的完整实际请求</button></div>${detail.complete_request_preview ? `<details open><summary>系统提示词、前文记忆和当前原文的完整拼装结果</summary><pre class="prompt-code runtime">${escapeHtml(detail.complete_request_preview)}</pre></details>` : ""}` : "";
  const draftControls = detail.status === "production"
    ? `<div class="prompt-editor"><label>修改说明<input id="prompt-change-note" maxlength="1000" placeholder="说明为什么要改，以及希望哪个测试发生变化"></label><label>核心约束<textarea id="prompt-core-edit" rows="14">${escapeHtml(core)}</textarea></label><label>任务补充规则<textarea id="prompt-task-edit" rows="8" placeholder="只写这项任务新增的规则">${escapeHtml(task)}</textarea></label><button id="prompt-create-draft" class="button button-primary" type="button">保存为草稿，不影响正式任务</button></div>`
    : `<div class="prompt-trial-controls">${isExtraction ? `<label>试跑章节<select id="prompt-trial-segment">${segmentOptions}</select></label><label>试跑模型<select id="prompt-trial-provider">${providerOptions}</select></label><button id="prompt-trial" class="button button-primary" type="button">只试跑这一章</button>` : '<p>这类提示词的试跑使用全书事实批次，发布前由对应回归门禁检查。</p>'}<button id="prompt-promote" class="button button-quiet" type="button">通过全库门禁后提升为正式版本</button>${detail.promoted_at ? '<button id="prompt-rollback" class="button button-danger" type="button">恢复这个历史正式版本</button>' : ""}</div>`;
  return `<section class="prompt-detail"><header><div><span class="eyebrow">${escapeHtml(detail.task_label)}</span><h3>${escapeHtml(detail.version)} · ${escapeHtml(detail.status)}</h3><p>哈希 ${escapeHtml(detail.prompt_hash.slice(0, 16))} · 约 ${Number(detail.estimated_tokens).toLocaleString()} 个输入标记</p></div><button id="prompt-detail-close" class="icon-button" type="button" aria-label="关闭提示词详情">×</button></header><div class="prompt-layer-grid">${detail.layers.map((layer) => `<article><strong>${escapeHtml(layer.label)}</strong><small>${layer.key === "external_facts" ? "与原文证据隔离，不注入抽取" : `${Number(layer.text?.length || 0).toLocaleString()} 字符`}</small></article>`).join("")}</div>${detail.diff ? `<details><summary>查看与正式版本的差异</summary><pre class="prompt-code">${escapeHtml(detail.diff)}</pre></details>` : ""}<details open><summary>查看最终系统提示词</summary><pre class="prompt-code">${escapeHtml(detail.system_prompt)}</pre></details>${runtimeControls}${draftControls}<div id="prompt-action-result" class="control-result"></div></section>`;
}

function renderCollaboration() {
  if (!state.controlPlane || Number(state.controlPlaneBookId) !== Number(state.bookId)) {
    refreshControlPlane();
    return;
  }
  const control = state.controlPlane;
  const contract = control.contract;
  const benchmark = control.benchmark;
  const evaluation = control.evaluation_progress;
  const totalConfirmed = Number(evaluation.confirmed_cases || 0);
  const promptCards = control.prompt_bundles.map((item) => `<button class="control-card prompt-open" data-task="${escapeHtml(item.task_key)}" type="button"><span>${escapeHtml(item.task_label)}</span><strong>${escapeHtml(item.version)}</strong><small>${escapeHtml(item.prompt_hash.slice(0, 12))} · 约 ${Number(item.estimated_tokens).toLocaleString()} 标记</small></button>`).join("");
  const feedback = control.collaboration.map((item) => {
    const next = item.status === "interpreted" ? (item.requires_confirmation ? "confirmed" : "implementing") : item.status === "confirmed" ? "implementing" : item.status === "implementing" ? "validating" : item.status === "validating" ? "released" : "";
    return `<article class="collaboration-item"><div><span class="control-status ${escapeHtml(item.status)}">${escapeHtml(collaborationStatusLabels[item.status] || item.status)}</span><strong>${escapeHtml(item.original_text)}</strong><p>${escapeHtml(item.interpreted_goal)}</p><small>验收：${item.acceptance.map(escapeHtml).join(" · ")}</small><small>影响：${item.impact.map(escapeHtml).join(" · ") || "尚未登记"}</small>${item.regression_case_id ? `<small>永久回归案例 #${item.regression_case_id}</small>` : ""}</div>${next ? `<button class="button button-quiet collaboration-next" data-id="${item.id}" data-status="${next}" type="button">${next === "confirmed" ? "确认理解" : next === "released" ? "确认验收并发布" : escapeHtml(collaborationStatusLabels[next])}</button>` : ""}</article>`;
  }).join("");
  const rules = control.domain_rules.map((rule) => `<article class="rule-item ${rule.active ? "" : "inactive"}"><div><strong>${escapeHtml(rule.statement)}</strong><small>${escapeHtml(rule.task_key)} · 优先级 ${rule.priority} · 版本 ${rule.version}${rule.rationale ? ` · ${escapeHtml(rule.rationale)}` : ""}</small></div><div class="benchmark-actions"><button class="button button-quiet rule-toggle" data-id="${rule.id}" data-active="${rule.active ? "false" : "true"}" type="button">${rule.active ? "停用" : "启用"}</button><button class="button button-danger rule-delete" data-id="${rule.id}" type="button">删除</button></div></article>`).join("") || '<p class="benchmark-empty">还没有用户阅读规则，当前只使用经过测试的核心提示词。</p>';
  const facts = control.external_facts.map((fact) => `<article class="rule-item ${fact.active ? "" : "inactive"}"><div><strong>${escapeHtml(fact.statement)}</strong><small>来源：${escapeHtml(fact.source_label)} · 不注入原文抽取${fact.source_url ? ` · ${escapeHtml(fact.source_url)}` : ""}</small></div><div class="benchmark-actions"><button class="button button-quiet fact-toggle" data-id="${fact.id}" data-active="${fact.active ? "false" : "true"}" type="button">${fact.active ? "停用" : "启用"}</button><button class="button button-danger fact-delete" data-id="${fact.id}" type="button">删除</button></div></article>`).join("") || '<p class="benchmark-empty">没有登记作品外资料，模型只依据当前小说原文。</p>';
  const routes = control.model_routes.map((route) => `<article class="route-card ${route.eligible ? "eligible" : ""}"><span>${escapeHtml(route.provider)}</span><strong>${escapeHtml(route.model)}</strong><small>${route.eligible ? "已经通过赛马" : "尚未取得自动路由资格"} · ${route.enabled ? "已启用" : "已停用"} · 优先级 ${route.priority} · 连续失败 ${route.consecutive_failures}</small><div class="route-actions"><button class="button button-quiet route-toggle" data-provider="${escapeHtml(route.provider)}" data-enabled="${route.enabled ? "false" : "true"}" type="button">${route.enabled ? "停用" : "启用"}</button>${route.consecutive_failures ? `<button class="button button-quiet route-reset" data-provider="${escapeHtml(route.provider)}" type="button">复位熔断</button>` : ""}</div></article>`).join("");
  const promptHistory = control.prompt_versions.map((version) => `<button class="version-row prompt-version-open" data-task="${escapeHtml(version.task_key)}" data-id="${version.id}" type="button"><span>${escapeHtml(version.task_key)} · ${escapeHtml(version.version)}</span><strong>${escapeHtml(version.status)}</strong><small>${escapeHtml(version.change_note || "未填写修改说明")} · ${escapeHtml(version.prompt_hash.slice(0, 12))}</small></button>`).join("");
  const runs = control.runs.map((run) => `<details class="run-item"><summary><span>${escapeHtml(run.run_kind)}</span><strong>${escapeHtml(run.provider)} · ${escapeHtml(run.model)}</strong><small>${escapeHtml(run.status)} · 输入 ${Number(run.input_tokens || 0).toLocaleString()} · 输出 ${Number(run.output_tokens || 0).toLocaleString()} · ${run.estimated_cost_usd == null ? "订阅通道不换算美元" : formatCost(run)}</small></summary><div><p>合同 ${escapeHtml(run.contract_version)} · 提示词 ${escapeHtml(run.prompt_hash.slice(0, 16))}</p><p>评估集 ${escapeHtml(run.eval_suite_version)} · 数据结构 ${escapeHtml(run.schema_version)}</p><pre class="prompt-code compact">${escapeHtml(JSON.stringify({ validation: run.validation, conflicts: run.conflicts }, null, 2))}</pre></div></details>`).join("") || '<p class="benchmark-empty">还没有使用新版运行清单发起模型任务。</p>';
  $("#view-panel").innerHTML = panelHead("协作控制", "要求、提示词、规则、模型、成本、验收和回归测试集中在同一页面。") + `<div class="control-plane"><section class="contract-banner"><div><span class="eyebrow">当前正式合同 · ${escapeHtml(contract.version)}</span><h3>${escapeHtml(contract.title)}</h3><p>${escapeHtml(contract.goal)}</p></div><div class="contract-metrics"><span><strong>100%</strong>关键案例</span><span><strong>95%</strong>保留集门槛</span><span><strong>${totalConfirmed}/300</strong>全库金标准</span></div></section><section class="evaluation-readiness ${evaluation.release_gate_passed ? "ready" : "blocked"}"><header><div><h3>${evaluation.release_gate_passed ? "正式发布门禁已通过" : "正式发布门禁尚未通过"}</h3><p>这是一条硬门禁。未达到公开样本规模时，任何模型都不会被标记为正式达标。</p></div><strong>${evaluation.holdout_accuracy_percent == null ? "尚无保留集结果" : `保留集 ${escapeHtml(evaluation.holdout_accuracy_percent)}%`}</strong></header><div class="evaluation-grid"><span>已确认案例 <strong>${evaluation.confirmed_cases}/300</strong></span><span>真实作品 <strong>${evaluation.book_count}/5 · ${evaluation.books_below_minimum_cases} 本不足 20 条</strong></span><span>保留集 <strong>${evaluation.holdout_cases} · ${evaluation.holdout_share_percent}%</strong></span><span>关键失败 <strong>${evaluation.critical_failures}</strong></span><span>证据逐字命中 <strong>${evaluation.quote_integrity_percent}%</strong></span><span>未解决冲突 <strong>${evaluation.unresolved_conflicts}</strong></span></div></section><div class="security-reminder"><strong>密钥轮换待办</strong><span>旧密钥曾出现在对话中。应用已经保证它们只在 Windows 加密存储中使用，但新密钥仍需在供应商账户后台生成后替换。</span></div><section class="control-section"><header><div><h3>反馈与验收闭环</h3><p>先保留原话，再明确系统理解和可直接检查的结果。</p></div></header><details class="control-form"><summary>登记新的反馈</summary><div class="control-form-grid"><label>你的原话<textarea id="collaboration-original" rows="3"></textarea></label><label>系统应当怎样理解<textarea id="collaboration-goal" rows="3"></textarea></label><label>验收条件，每行一条<textarea id="collaboration-acceptance" rows="4"></textarea></label><label>影响范围，每行一项<textarea id="collaboration-impact" rows="4"></textarea></label><label class="benchmark-critical"><input id="collaboration-confirm" type="checkbox"> 这项内容涉及目标、核心提示词、成本或发布，需要先确认</label><button id="collaboration-save" class="button button-primary" type="button">保存理解卡片</button></div></details><div class="collaboration-list">${feedback}</div></section><section class="control-section"><header><div><h3>完整提示词</h3><p>点击查看最终文本、分层来源、版本差异和单片段试跑。</p></div></header><div class="control-card-grid">${promptCards}</div>${promptDetailPanel()}<details class="prompt-history"><summary>查看全部提示词版本和回滚入口</summary><div class="version-list">${promptHistory}</div></details></section><section class="control-section two-column"><div><header><h3>阅读规则</h3><p>只能写分析方法，保存后自动进入最终提示词预览。</p></header><div class="inline-control-form"><textarea id="domain-rule-statement" rows="3" placeholder="请使用陈述句，例如：明确出现父母称谓时，应当检查对象能否唯一对应现有人物"></textarea><select id="domain-rule-task"><option value="all">全部任务</option><option value="extraction">片段抽取</option><option value="global_review">全书整理</option><option value="connectivity_audit">关系复审</option></select><button id="domain-rule-save" class="button button-primary" type="button">添加阅读规则</button></div><div class="rule-list">${rules}</div></div><div><header><h3>外部事实</h3><p>单独保存和标明来源，永远不成为小说原文证据。</p></header><div class="inline-control-form"><textarea id="external-fact-statement" rows="3" placeholder="作品外资料"></textarea><input id="external-fact-source" maxlength="300" placeholder="资料来源"><input id="external-fact-url" maxlength="1000" placeholder="来源地址，可不填"><button id="external-fact-save" class="button button-primary" type="button">保存外部资料</button></div><div class="rule-list">${facts}</div></div></section><section class="control-section"><header><div><h3>模型赛马与自动路由</h3><p>同一片段、同一提示词、同一金标准，关键案例失败就不能取得资格。</p></div><button id="model-race" class="button button-primary" type="button">运行一次真实单片段赛马</button></header><div class="route-grid">${routes}</div><div id="model-race-result" class="control-result"></div></section><section class="control-section"><header><h3>运行清单</h3><p>ChatGPT 登录通道不伪造单次美元费用。</p></header><div class="run-list">${runs}</div></section></div>`;
  bindCollaborationControls();
}

async function loadPromptDetail(taskKey, bundleId = null, segmentId = null) {
  try {
    const parameters = new URLSearchParams();
    if (bundleId) parameters.set("bundle_id", bundleId);
    if (segmentId) parameters.set("segment_id", segmentId);
    const query = parameters.size ? `?${parameters.toString()}` : "";
    state.promptDetail = await api(`/api/books/${state.bookId}/prompt-bundles/${taskKey}${query}`);
    renderCollaboration();
    $(".prompt-detail")?.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) { toast(error.message, true); }
}

function nonEmptyLines(value) {
  return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}

async function createPromptDraftFromUi() {
  const note = $("#prompt-change-note").value.trim();
  if (!note) return toast("请先说明为什么修改提示词。", true);
  try {
    const draft = await api(`/api/prompt-bundles/${state.promptDetail.task_key}/drafts?book_id=${state.bookId}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ core_text: $("#prompt-core-edit").value, task_text: $("#prompt-task-edit").value, change_note: note }),
    });
    toast("提示词草稿已经保存，正式任务没有受到影响。");
    await refreshControlPlane(true);
    await loadPromptDetail(draft.task_key, draft.id);
  } catch (error) { toast(error.message, true); }
}

async function trialPromptFromUi() {
  const button = $("#prompt-trial");
  button.disabled = true;
  button.textContent = "正在隔离试跑…";
  try {
    const result = await api(`/api/prompt-bundles/${state.promptDetail.id}/trial`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ book_id: state.bookId, segment_id: Number($("#prompt-trial-segment").value), provider: $("#prompt-trial-provider").value }),
    });
    $("#prompt-action-result").innerHTML = `<strong>试跑完成</strong><p>引文逐字命中 ${escapeHtml(result.validation.quote_integrity_percent)}% · 输入 ${Number(result.input_tokens).toLocaleString()} · 输出 ${Number(result.output_tokens).toLocaleString()} · ${result.estimated_cost_usd == null ? "订阅通道不换算美元" : escapeHtml(formatCost(result))}</p>`;
    await refreshControlPlane(true);
  } catch (error) { toast(error.message, true); button.disabled = false; button.textContent = "只试跑这一章"; }
}

async function promotePromptFromUi() {
  if (!window.confirm("确认把这版提示词提升为正式版本吗？以后新任务会使用它，旧结果仍可按版本追溯。")) return;
  try {
    const result = await api(`/api/prompt-bundles/${state.promptDetail.id}/promote?book_id=${state.bookId}`, { method: "POST" });
    toast(`提示词 ${result.version} 已经成为正式版本。`);
    state.promptDetail = null;
    await refreshControlPlane();
  } catch (error) { toast(error.message, true); }
}

async function rollbackPromptFromUi() {
  if (!window.confirm("确认恢复这个历史正式版本吗？当前正式版本会归档，所有历史仍然保留。")) return;
  try {
    const result = await api(`/api/prompt-bundles/${state.promptDetail.id}/rollback?book_id=${state.bookId}&confirmed=true`, { method: "POST" });
    toast(`已经恢复提示词 ${result.version}。`);
    state.promptDetail = null;
    await refreshControlPlane();
  } catch (error) { toast(error.message, true); }
}

function bindCollaborationControls() {
  $$(".prompt-open").forEach((button) => button.addEventListener("click", () => loadPromptDetail(button.dataset.task)));
  $$(".prompt-version-open").forEach((button) => button.addEventListener("click", () => loadPromptDetail(button.dataset.task, Number(button.dataset.id))));
  $("#prompt-detail-close")?.addEventListener("click", () => { state.promptDetail = null; renderCollaboration(); });
  $("#prompt-create-draft")?.addEventListener("click", createPromptDraftFromUi);
  $("#prompt-trial")?.addEventListener("click", trialPromptFromUi);
  $("#prompt-promote")?.addEventListener("click", promotePromptFromUi);
  $("#prompt-rollback")?.addEventListener("click", rollbackPromptFromUi);
  $("#prompt-runtime-preview")?.addEventListener("click", () => loadPromptDetail(state.promptDetail.task_key, state.promptDetail.id, Number($("#prompt-runtime-segment").value)));
  $("#collaboration-save")?.addEventListener("click", async () => {
    const original = $("#collaboration-original").value.trim();
    const goal = $("#collaboration-goal").value.trim();
    const acceptance = nonEmptyLines($("#collaboration-acceptance").value);
    if (!original || !goal || !acceptance.length) return toast("请填写原话、系统理解和至少一条验收条件。", true);
    try {
      await api(`/api/books/${state.bookId}/collaboration`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ original_text: original, interpreted_goal: goal, acceptance, impact: nonEmptyLines($("#collaboration-impact").value), requires_confirmation: $("#collaboration-confirm").checked }) });
      await refreshControlPlane();
    } catch (error) { toast(error.message, true); }
  });
  $$(".collaboration-next").forEach((button) => button.addEventListener("click", async () => {
    try { await api(`/api/collaboration/${button.dataset.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: button.dataset.status }) }); await refreshControlPlane(); } catch (error) { toast(error.message, true); }
  }));
  $("#domain-rule-save")?.addEventListener("click", async () => {
    const statement = $("#domain-rule-statement").value.trim();
    if (!statement) return toast("请先写一条阅读规则。", true);
    try { await api(`/api/books/${state.bookId}/domain-rules`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ statement, task_key: $("#domain-rule-task").value }) }); state.promptDetail = null; await refreshControlPlane(); } catch (error) { toast(error.message, true); }
  });
  $$(".rule-toggle").forEach((button) => button.addEventListener("click", async () => { try { await api(`/api/domain-rules/${button.dataset.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ active: button.dataset.active === "true" }) }); state.promptDetail = null; await refreshControlPlane(); } catch (error) { toast(error.message, true); } }));
  $$(".rule-delete").forEach((button) => button.addEventListener("click", async () => { if (!window.confirm("删除这条阅读规则吗？")) return; try { await api(`/api/domain-rules/${button.dataset.id}`, { method: "DELETE" }); state.promptDetail = null; await refreshControlPlane(); } catch (error) { toast(error.message, true); } }));
  $("#external-fact-save")?.addEventListener("click", async () => {
    const statement = $("#external-fact-statement").value.trim(); const source = $("#external-fact-source").value.trim();
    if (!statement || !source) return toast("外部资料必须同时填写内容和来源。", true);
    try { await api(`/api/books/${state.bookId}/external-facts`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ statement, source_label: source, source_url: $("#external-fact-url").value.trim() }) }); await refreshControlPlane(); } catch (error) { toast(error.message, true); }
  });
  $$(".fact-toggle").forEach((button) => button.addEventListener("click", async () => { try { await api(`/api/external-facts/${button.dataset.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ active: button.dataset.active === "true" }) }); await refreshControlPlane(); } catch (error) { toast(error.message, true); } }));
  $$(".fact-delete").forEach((button) => button.addEventListener("click", async () => { if (!window.confirm("删除这条外部资料吗？")) return; try { await api(`/api/external-facts/${button.dataset.id}`, { method: "DELETE" }); await refreshControlPlane(); } catch (error) { toast(error.message, true); } }));
  $$(".route-toggle").forEach((button) => button.addEventListener("click", async () => { try { await api(`/api/model-routes/${button.dataset.provider}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: button.dataset.enabled === "true" }) }); await refreshControlPlane(); } catch (error) { toast(error.message, true); } }));
  $$(".route-reset").forEach((button) => button.addEventListener("click", async () => { try { await api(`/api/model-routes/${button.dataset.provider}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reset_circuit: true }) }); await refreshControlPlane(); } catch (error) { toast(error.message, true); } }));
  $("#model-race")?.addEventListener("click", async () => {
    if (!window.confirm("这会让所有当前可用候选模型分析同一个片段。API 会产生费用，Codex Luna 会消耗 ChatGPT 计划额度。继续吗？")) return;
    const button = $("#model-race"); button.disabled = true; button.textContent = "正在运行同片段赛马…";
    const candidates = state.providers.filter((item) => item.available && ["deepseek", "moonshot", "codex_luna"].includes(item.id)).map((item) => item.id);
    try {
      const result = await api(`/api/books/${state.bookId}/model-races`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ providers: candidates.length ? candidates : ["mock"], run_live_canary: true }) });
      $("#model-race-result").innerHTML = result.reports.map((item) => `<article><strong>${escapeHtml(item.provider)} · ${escapeHtml(item.status)}</strong><span>准确率 ${item.accuracy_percent ?? "未建立"}% · 引文 ${item.evidence_percent ?? "未运行"}% · ${item.estimated_cost_usd == null ? "订阅或未知价格" : escapeHtml(formatCost(item))}</span></article>`).join("");
      await refreshControlPlane(true);
    } catch (error) { toast(error.message, true); } finally { button.disabled = false; button.textContent = "运行一次真实单片段赛马"; }
  });
}

const benchmarkTypeLabels = {
  identity_same: "两个人名指向同一人物",
  identity_distinct: "两个人名必须区分",
  event_present: "指定事件已经出现",
  event_before: "两个事件的故事先后",
  main_subject: "主线人物",
  journey_start: "主线行程起点",
  segment_accounting: "原文片段覆盖",
  fact_evidence: "结构记录证据覆盖",
  quote_integrity: "引文逐字存在",
};

function benchmarkExpectedText(caseType, expected = {}) {
  if (expected.withheld) return "保留测试答案已隐藏，只参与正式发布门禁";
  if (caseType === "identity_same") return "同一人物：" + (expected.left || "—") + " 与 " + (expected.right || "—");
  if (caseType === "identity_distinct") return "必须区分：" + (expected.left || "—") + " 与 " + (expected.right || "—");
  if (caseType === "event_present") return "事件“" + (expected.title || "—") + "”不晚于第 " + (Number(expected.max_segment || 0) + 1) + " 章";
  if (caseType === "event_before") return "“" + (expected.earlier || "—") + "”早于“" + (expected.later || "—") + "”";
  if (caseType === "main_subject") return "主线人物是“" + (expected.name || "—") + "”";
  if (caseType === "journey_start") return "主线行程不晚于第 " + (Number(expected.max_segment || 0) + 1) + " 章开始";
  return "覆盖率不低于 " + (expected.percent ?? "—") + "%";
}

function benchmarkExpectedFields(caseType, expected = {}) {
  const textInput = (id, label, placeholder, value = "") => `<label>${label}<input id="${id}" maxlength="240" value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}"></label>`;
  const chapterInput = (id, label, value = 0) => `<label>${label}<input id="${id}" type="number" min="1" max="${state.overview.segments.length}" value="${Number(value) + 1}"></label>`;
  if (caseType === "identity_same" || caseType === "identity_distinct") {
    return `${textInput("benchmark-left", "名称一", "例如：孙悟空", expected.left)}${textInput("benchmark-right", "名称二", "例如：齐天大圣", expected.right)}`;
  }
  if (caseType === "event_present") {
    return `${textInput("benchmark-event-title", "事件关键词", "例如：石猴出世", expected.title)}${chapterInput("benchmark-max-segment", "最晚出现章节", expected.max_segment)}`;
  }
  if (caseType === "event_before") {
    return `${textInput("benchmark-earlier", "较早事件关键词", "例如：石猴出世", expected.earlier)}${textInput("benchmark-later", "较晚事件关键词", "例如：大闹天宫", expected.later)}`;
  }
  if (caseType === "main_subject") return textInput("benchmark-subject-name", "主线人物名称", "例如：孙悟空", expected.name);
  if (caseType === "journey_start") return chapterInput("benchmark-max-segment", "最晚起始章节", expected.max_segment);
  return `<label>最低百分比<input id="benchmark-percent" type="number" min="0" max="100" step="0.01" value="${expected.percent ?? 100}"></label>`;
}

function benchmarkExpectedFromForm(caseType) {
  const text = (id) => $(id)?.value.trim() || "";
  const chapter = (id) => Math.max(0, Number($(id)?.value || 1) - 1);
  if (caseType === "identity_same" || caseType === "identity_distinct") return { left: text("#benchmark-left"), right: text("#benchmark-right") };
  if (caseType === "event_present") return { title: text("#benchmark-event-title"), max_segment: chapter("#benchmark-max-segment") };
  if (caseType === "event_before") return { earlier: text("#benchmark-earlier"), later: text("#benchmark-later") };
  if (caseType === "main_subject") return { name: text("#benchmark-subject-name") };
  if (caseType === "journey_start") return { max_segment: chapter("#benchmark-max-segment") };
  return { percent: Number($("#benchmark-percent")?.value || 0) };
}

function renderBenchmarkPanel() {
  const editing = state.benchmarkEditingId
    ? state.benchmarks.find((item) => Number(item.id) === Number(state.benchmarkEditingId))
    : null;
  const caseType = editing?.case_type || "identity_same";
  const sourceSegment = editing?.source_segment ?? Number(state.overview.through_segment || 0);
  const sourceOptions = state.overview.segments.map((segment) =>
    `<option value="${segment.ordinal}" ${Number(segment.ordinal) === Number(sourceSegment) ? "selected" : ""}>${escapeHtml(chapterForSegment(segment.ordinal))}</option>`
  ).join("");
  const passed = state.benchmarks.filter((item) => item.passed === true).length;
  const summary = state.benchmarks.length ? `${passed}/${state.benchmarks.length} 条通过` : "还没有人工登记的金标准";
  const rows = state.benchmarks.length ? state.benchmarks.map((item) => {
    const status = item.holdout ? "保留测试" : item.passed === true ? "通过" : item.passed === false ? "未通过" : "待复算";
    const statusClass = item.passed === true ? "passed" : item.passed === false ? "failed" : "pending";
    return `<article class="benchmark-case"><div><span class="benchmark-status ${statusClass}">${status}</span><strong>${escapeHtml(item.subject)}</strong><small>${escapeHtml(benchmarkTypeLabels[item.case_type] || item.case_type)} · ${escapeHtml(chapterForSegment(item.source_segment))}${item.critical ? " · 主体关键项" : " · 普通项"} · ${escapeHtml(item.suite_name || "book-gold")}${item.failure_category ? ` · ${escapeHtml(item.failure_category)}` : ""}</small><p>${escapeHtml(benchmarkExpectedText(item.case_type, item.expected))}</p><p class="benchmark-note">${item.holdout ? "答案在普通调试流程中保持隐藏" : `原文核对：${escapeHtml(item.note)}`}</p></div><div class="benchmark-actions"><button class="button button-quiet benchmark-source" data-segment="${item.source_segment}" type="button">查看原文</button>${item.holdout ? "" : `<button class="button button-quiet benchmark-edit" data-id="${item.id}" type="button">编辑</button>`}<button class="button button-danger benchmark-delete" data-id="${item.id}" type="button">删除</button></div></article>`;
  }).join("") : '<p class="benchmark-empty">独立人工核对后，在这里登记期望结论和原文章节。保存后立即本地复算，不调用模型。</p>';
  const candidateRows = state.benchmarkCandidates.length ? state.benchmarkCandidates.map((item) => {
    const evidence = Array.isArray(item.evidence) ? item.evidence : [];
    const provenance = evidence.length
      ? `逐字证据 ${evidence.length} 条 · ${escapeHtml(evidence.map((entry) => entry.chapter_title).filter(Boolean).slice(0, 2).join("、"))}`
      : "本地检查候选 · 未附逐字引文";
    return `<article class="benchmark-case candidate"><div><span class="benchmark-status pending">待确认</span><strong>${escapeHtml(item.subject)}</strong><small>${escapeHtml(benchmarkTypeLabels[item.case_type] || item.case_type)} · ${escapeHtml(chapterForSegment(item.source_segment))}${item.critical ? " · 建议作为关键项" : " · 普通项"}</small><p>${escapeHtml(benchmarkExpectedText(item.case_type, item.expected))}</p><p class="benchmark-note">${provenance}</p><p class="benchmark-note">${escapeHtml(item.note)}</p></div><div class="benchmark-actions"><button class="button button-quiet benchmark-candidate-source" data-segment="${item.source_segment}" type="button">查看原文</button><button class="button button-primary benchmark-candidate-accept" data-id="${item.id}" type="button">确认并计入</button><button class="button button-danger benchmark-candidate-reject" data-id="${item.id}" type="button">不采用</button></div></article>`;
  }).join("") : '<p class="benchmark-empty">还没有可复核候选。先完成至少一个带逐字证据的片段分析，再从已有证据生成候选。</p>';
  const candidatePanel = `<details class="benchmark-form"><summary>候选与人工确认</summary><div class="benchmark-head"><div><p>带引文的候选来自原文证据；本地检查候选会明确标识。点击确认后才成为金标准，拒绝不会影响小说数据。</p></div><button id="benchmark-candidate-refresh" class="button button-quiet" type="button">刷新候选和引文</button></div><div class="benchmark-list">${candidateRows}</div></details>`;
  return `<section class="benchmark-panel"><header class="benchmark-head"><div><h3>人工金标准</h3><p>用于验证真实书籍是否达到准确率门禁，${escapeHtml(summary)}，本地复算费用为 $0。</p></div><button id="benchmark-evaluate" class="button button-quiet" type="button">重新本地复算</button></header>${candidatePanel}<details class="benchmark-form" open><summary>${editing ? "编辑人工金标准" : "登记人工金标准"}</summary><div class="benchmark-form-grid"><label>核对类型<select id="benchmark-case-type">${Object.entries(benchmarkTypeLabels).map(([value, label]) => `<option value="${value}" ${value === caseType ? "selected" : ""}>${escapeHtml(label)}</option>`).join("")}</select></label><label>核对说明<input id="benchmark-case-subject" maxlength="240" value="${escapeHtml(editing?.subject || "")}" placeholder="例如：孙悟空与齐天大圣是同一人物"></label><div id="benchmark-expected-fields" class="benchmark-expected-fields">${benchmarkExpectedFields(caseType, editing?.expected || {})}</div><label>原文章节<select id="benchmark-source-segment">${sourceOptions}</select></label><label>评估集名称<input id="benchmark-suite" maxlength="120" value="${escapeHtml(editing?.suite_name || "real-novel-gold")}" placeholder="例如：real-novel-gold"></label><label>错误类别<input id="benchmark-failure-category" maxlength="120" value="${escapeHtml(editing?.failure_category || "")}" placeholder="例如：亲属关系、时间倒叙或地点方位"></label><label class="benchmark-note-field">原文核对说明<textarea id="benchmark-note" maxlength="1000" rows="3" placeholder="说明你依据哪个原文事实作出判断">${escapeHtml(editing?.note || "")}</textarea></label><label class="benchmark-critical"><input id="benchmark-critical" type="checkbox" ${editing?.critical !== false ? "checked" : ""}> 这是主体关键项，失败时不能通过最终门禁</label><label class="benchmark-critical"><input id="benchmark-holdout" type="checkbox" ${editing?.holdout ? "checked" : ""}> 保存为保留测试，之后在普通调试页面隐藏答案</label><div class="benchmark-actions"><button id="benchmark-save" class="button button-primary" type="button">${editing ? "保存并重新核验" : "登记并本地核验"}</button>${editing ? '<button id="benchmark-cancel" class="button button-quiet" type="button">取消编辑</button>' : ""}</div></div></details><div class="benchmark-list">${rows}</div></section>`;
}

function refreshBenchmarkExpectedFields() {
  const type = $("#benchmark-case-type")?.value;
  const current = state.benchmarkEditingId
    ? state.benchmarks.find((item) => Number(item.id) === Number(state.benchmarkEditingId))
    : null;
  $("#benchmark-expected-fields").innerHTML = benchmarkExpectedFields(type, current?.case_type === type ? current.expected : {});
}

async function saveBenchmarkCase() {
  const type = $("#benchmark-case-type").value;
  const subject = $("#benchmark-case-subject").value.trim();
  const note = $("#benchmark-note").value.trim();
  if (!subject || !note) {
    toast("请填写核对说明和原文核对依据。", true);
    return;
  }
  const payload = {
    case_type: type,
    subject,
    expected: benchmarkExpectedFromForm(type),
    source_segment: Number($("#benchmark-source-segment").value),
    note,
    critical: $("#benchmark-critical").checked,
    suite_name: $("#benchmark-suite").value.trim() || "real-novel-gold",
    failure_category: $("#benchmark-failure-category").value.trim(),
    holdout: $("#benchmark-holdout").checked,
    confirmed_by_user: true,
  };
  const button = $("#benchmark-save");
  button.disabled = true;
  button.textContent = "正在本地核验…";
  try {
    if (state.benchmarkEditingId) {
      await api(`/api/benchmarks/${state.benchmarkEditingId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } else {
      await api(`/api/books/${state.bookId}/benchmarks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    }
    state.benchmarkEditingId = null;
    toast("人工金标准已保存并完成本地复算，费用 $0。");
    await loadOverview(Number($("#progress-slider").value), true);
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
    button.textContent = state.benchmarkEditingId ? "保存并重新核验" : "登记并本地核验";
  }
}

async function evaluateBenchmarkCases() {
  const button = $("#benchmark-evaluate");
  button.disabled = true;
  button.textContent = "正在本地复算…";
  try {
    const result = await api(`/api/books/${state.bookId}/benchmarks/evaluate`, { method: "POST" });
    toast(`本地复算完成：${result.summary.passed}/${result.summary.total} 条通过，费用 $0。`);
    await loadOverview(Number($("#progress-slider").value), true);
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
    button.textContent = "重新本地复算";
  }
}

async function refreshBenchmarkCandidates() {
  const button = $("#benchmark-candidate-refresh");
  button.disabled = true;
  button.textContent = "正在整理候选…";
  try {
    const result = await api(`/api/books/${state.bookId}/benchmark-candidates/refresh`, { method: "POST" });
    toast(`已新增 ${result.created} 条待确认候选，目前待确认 ${result.pending} 条。`);
    await loadOverview(Number($("#progress-slider").value), true);
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
    button.textContent = "刷新候选和引文";
  }
}

async function resolveBenchmarkCandidate(candidateId, action) {
  const note = action === "accept"
    ? window.prompt("写下确认依据，可留空并使用候选的原文核对说明。")
    : window.prompt("写下拒绝原因，避免同类候选再次混入金标准。");
  if (note === null) return;
  try {
    await api(`/api/benchmark-candidates/${candidateId}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, note }),
    });
    toast(action === "accept" ? "候选已经人工确认并计入金标准。" : "候选已经拒绝，不会计入准确率。");
    await loadOverview(Number($("#progress-slider").value), true);
  } catch (error) {
    toast(error.message, true);
  }
}

function bindBenchmarkPanel() {
  $("#benchmark-case-type")?.addEventListener("change", refreshBenchmarkExpectedFields);
  $("#benchmark-save")?.addEventListener("click", saveBenchmarkCase);
  $("#benchmark-evaluate")?.addEventListener("click", evaluateBenchmarkCases);
  $("#benchmark-candidate-refresh")?.addEventListener("click", refreshBenchmarkCandidates);
  $("#benchmark-cancel")?.addEventListener("click", () => {
    state.benchmarkEditingId = null;
    renderQuality();
  });
  $$(".benchmark-source").forEach((button) => button.addEventListener("click", () => {
    const segment = state.overview.segments.find((item) => Number(item.ordinal) === Number(button.dataset.segment));
    if (segment) openSource(Number(segment.id), "");
  }));
  $$(".benchmark-edit").forEach((button) => button.addEventListener("click", () => {
    state.benchmarkEditingId = Number(button.dataset.id);
    renderQuality();
  }));
  $$(".benchmark-candidate-source").forEach((button) => button.addEventListener("click", () => {
    const segment = state.overview.segments.find((item) => Number(item.ordinal) === Number(button.dataset.segment));
    if (segment) openSource(Number(segment.id), "");
  }));
  $$(".benchmark-candidate-accept").forEach((button) => button.addEventListener("click", () => {
    resolveBenchmarkCandidate(Number(button.dataset.id), "accept");
  }));
  $$(".benchmark-candidate-reject").forEach((button) => button.addEventListener("click", () => {
    resolveBenchmarkCandidate(Number(button.dataset.id), "reject");
  }));
  $$(".benchmark-delete").forEach((button) => button.addEventListener("click", async () => {
    if (!window.confirm("删除这条人工金标准吗？删除后会重新计算准确率门禁。")) return;
    try {
      await api(`/api/benchmarks/${button.dataset.id}`, { method: "DELETE" });
      if (Number(state.benchmarkEditingId) === Number(button.dataset.id)) state.benchmarkEditingId = null;
      toast("人工金标准已删除，并已重新计算准确率。");
      await loadOverview(Number($("#progress-slider").value), true);
    } catch (error) {
      toast(error.message, true);
    }
  }));
}

function renderQuality() {
  const quality = state.overview.quality;
  const cost = state.overview.cost_summary || {};
  const coverage = quality.evidence_coverage_percent === null ? "—" : `${quality.evidence_coverage_percent}%`;
  const costLabel = Number(cost.priced_job_count || 0) ? `$${Number(cost.estimated_cost_usd || 0).toFixed(6)}` : "暂无计价任务";
  const accuracy = quality.benchmark_accuracy_percent === null ? "未建立" : `${quality.benchmark_accuracy_percent}%`;
  const accuracyDetail = quality.accuracy_gate_required && quality.benchmark_cases < 20
    ? `只有 ${quality.benchmark_cases} 条人工金标准，至少需要 20 条`
    : `${quality.benchmark_passed}/${quality.benchmark_cases} 通过 · 主体错误 ${quality.critical_subject_failures}`;
  const metrics = `<div class="metric-grid quality-metrics"><article class="metric-card"><span>全书处理进度</span><strong>${quality.segments_processed}/${quality.segments_total}</strong><small>每个原文片段单独记账</small></article><article class="metric-card"><span>原文证据覆盖</span><strong>${coverage}</strong><small>${quality.facts_with_evidence}/${quality.facts_total} 条结构记录</small></article><article class="metric-card${quality.accuracy_gate_required && !quality.accuracy_gate_passed ? " warning" : ""}"><span>金标准准确率</span><strong>${escapeHtml(accuracy)}</strong><small>${escapeHtml(accuracyDetail)}</small></article><article class="metric-card"><span>累计分析费用</span><strong>${escapeHtml(costLabel)}</strong><small>${Number(cost.job_count || 0)} 次任务 · 输入 ${Number(cost.input_tokens || 0).toLocaleString()} · 输出 ${Number(cost.output_tokens || 0).toLocaleString()}</small></article><article class="metric-card"><span>供应商缓存命中</span><strong>${Number(cost.cache_hit_input_tokens || 0).toLocaleString()}</strong><small>另有完整请求本地复用，不产生调用</small></article><article class="metric-card${Number(quality.unresolved_merges || 0) > 0 ? " warning" : ""}"><span>证据不足的身份</span><strong>${quality.unresolved_merges}</strong><small>系统已避免自动合并，可选复核</small></article></div>`;
  const issues = quality.issues.length ? `<div class="quality-list">${quality.issues.map((issue) => `<article class="quality-issue ${escapeHtml(issue.level)}"><strong>${escapeHtml(issue.title)}</strong><p>${escapeHtml(issue.detail)}</p></article>`).join("")}</div>` : emptyState("当前自动检查没有发现问题", "自动检查只能验证证据、重复和结构一致性，人物理解仍可通过原文证据人工核验。");
  const jobs = state.overview.analysis_jobs || [];
  const jobHistory = jobs.length ? `<section class="cost-history"><h3>最近分析费用</h3><div class="table-wrap"><table class="data-table"><thead><tr><th>任务</th><th>模型</th><th>输入令牌</th><th>缓存命中</th><th>输出令牌</th><th>估算费用</th></tr></thead><tbody>${jobs.map((job) => `<tr><td>#${job.id} · ${escapeHtml(job.status)}</td><td>${escapeHtml(job.provider)} · ${escapeHtml(job.model)}</td><td>${Number(job.input_tokens || 0).toLocaleString()}</td><td>${Number(job.cache_hit_input_tokens || 0).toLocaleString()}</td><td>${Number(job.output_tokens || 0).toLocaleString()}</td><td><strong>${escapeHtml(formatCost(job))}</strong><small>${job.pricing_effective_date ? `价格日期 ${escapeHtml(job.pricing_effective_date)}` : escapeHtml(job.pricing_source || "价格未配置")}</small></td></tr>`).join("")}</tbody></table></div></section>` : "";
  const ledger = state.overview.cost_ledger || [];
  const ledgerTable = ledger.length ? `<section class="cost-history"><h3>逐次模型调用账本</h3><div class="table-wrap"><table class="data-table"><thead><tr><th>用途</th><th>模型</th><th>状态</th><th>输入</th><th>输出</th><th>本次费用</th></tr></thead><tbody>${ledger.map((call) => `<tr><td>${escapeHtml(call.purpose)}</td><td>${escapeHtml(call.provider)} · ${escapeHtml(call.model)}</td><td>${call.cache_hit ? "本地复用" : escapeHtml(call.status)}</td><td>${Number(call.input_tokens || 0).toLocaleString()}</td><td>${Number(call.output_tokens || 0).toLocaleString()}</td><td>${escapeHtml(formatCost(call))}</td></tr>`).join("")}</tbody></table></div></section>` : "";
  const unresolvedReviews = (state.overview.connectivity_reviews || []).filter((item) => ["pending", "ambiguous"].includes(item.status));
  const unresolvedLocations = (state.overview.event_location_reviews || []).filter((item) => item.status === "unresolved");
  const identityConflicts = state.overview.merge_candidates || [];
  const contradictions = (state.overview.contradictions || []).filter((item) => item.status === "unreviewed");
  const timeConflicts = state.overview.time_conflicts || [];
  const resolvedIdentities = (state.overview.identity_conflict_reviews || []).filter((item) => !["unreviewed", "needs_review"].includes(item.status));
  const resolvedContradictions = (state.overview.contradictions || []).filter((item) => item.status !== "unreviewed");
  const resolvedTimeConstraints = (state.overview.time_constraint_reviews || []).filter((item) => item.status !== "conflict");
  const autoConflictCount = identityConflicts.length + contradictions.length + timeConflicts.length;
  const resolvedConflictCount = resolvedIdentities.length + resolvedContradictions.length + resolvedTimeConstraints.length;
  const statusLabels = {
    auto_separate: "自动保持分离", rejected: "人工保持分离", accepted: "人工合并", auto_merged: "自动合并",
    auto_quarantined: "自动隔离", resolved_contextual: "不同情境", resolved_false_positive: "确认误报", quarantined: "人工隔离",
    auto_rejected: "自动舍弃约束",
  };
  const modelReviewItems = unresolvedReviews.length + unresolvedLocations.length;
  const resolvedHistory = resolvedConflictCount ? `<details class="conflict-history"><summary>查看 ${resolvedConflictCount} 条已处理记录</summary><div class="conflict-history-list">${resolvedIdentities.map((item) => `<article><div><strong>身份 · ${escapeHtml(item.left_name)} ↔ ${escapeHtml(item.right_name)}</strong><small>${escapeHtml(statusLabels[item.status] || item.status)} · ${escapeHtml(item.resolution_reason || item.reason)}</small></div>${["auto_separate", "rejected"].includes(item.status) ? `<div class="conflict-actions"><button class="button button-quiet merge-choice" data-keep="${item.left_entity_id}" data-remove="${item.right_entity_id}" type="button">改为合并到 ${escapeHtml(item.left_name)}</button><button class="button button-quiet merge-choice" data-keep="${item.right_entity_id}" data-remove="${item.left_entity_id}" type="button">改为合并到 ${escapeHtml(item.right_name)}</button></div>` : ""}</article>`).join("")}${resolvedContradictions.map((item) => `<article><div><strong>事实 · ${escapeHtml(item.left.label)} ↔ ${escapeHtml(item.right.label)}</strong><small>${escapeHtml(statusLabels[item.status] || item.status)} · ${escapeHtml(item.resolution_reason || item.summary)}</small></div><div class="conflict-actions"><button class="button button-quiet contradiction-action" data-id="${item.id}" data-action="contextual" type="button">改为不同情境</button><button class="button button-quiet contradiction-action" data-id="${item.id}" data-action="false_positive" type="button">改为误报</button><button class="button button-danger contradiction-action" data-id="${item.id}" data-action="quarantine" type="button">改为隔离</button></div></article>`).join("")}${resolvedTimeConstraints.map((item) => `<article><div><strong>时间 · ${escapeHtml(item.earlier_title)} → ${escapeHtml(item.later_title)}</strong><small>${escapeHtml(statusLabels[item.status] || item.status)} · ${escapeHtml(item.resolution_reason || item.reason)}</small></div><div class="conflict-actions"><button class="button button-quiet time-conflict-action" data-id="${item.id}" data-action="reverse" type="button">改为反转</button><button class="button button-danger time-conflict-action" data-id="${item.id}" data-action="reject" type="button">改为舍弃</button><button class="button button-quiet time-conflict-action" data-id="${item.id}" data-action="quarantine" type="button">改为隔离</button></div></article>`).join("")}</div></details>` : "";
  const conflictPanel = `<section class="conflict-center"><div class="conflict-center-head"><div><h3>冲突处理中心</h3><p>自动处理只使用本地规则，不调用模型、不删除原始事实。证据不足的身份保持分离，事实冲突进入隔离区，循环时间约束会被舍弃。</p></div>${autoConflictCount ? `<button id="quality-auto-close" class="button button-primary" type="button">免费自动处理 ${autoConflictCount} 项</button>` : '<span class="conflict-zero">当前无需处理</span>'}</div>${identityConflicts.length ? `<section class="conflict-group"><h4>身份候选 · ${identityConflicts.length}</h4><div class="conflict-card-list">${identityConflicts.map((item) => `<article class="conflict-card"><div><strong>${escapeHtml(item.left_name)} ↔ ${escapeHtml(item.right_name)}</strong><p>${escapeHtml(item.reason)} · 把握 ${Math.round(Number(item.confidence || 0) * 100)}%</p></div><div class="conflict-actions"><button class="button button-quiet merge-choice" data-keep="${item.left_entity_id}" data-remove="${item.right_entity_id}" type="button">合并为 ${escapeHtml(item.left_name)}</button><button class="button button-quiet merge-choice" data-keep="${item.right_entity_id}" data-remove="${item.left_entity_id}" type="button">合并为 ${escapeHtml(item.right_name)}</button><button class="button button-danger merge-reject" data-id="${item.id}" type="button">保持两个身份</button></div></article>`).join("")}</div></section>` : ""}${contradictions.length ? `<section class="conflict-group"><h4>事实冲突 · ${contradictions.length}</h4><div class="conflict-card-list">${contradictions.map((item) => `<article class="conflict-card"><div><strong>${escapeHtml(item.left.label)} ↔ ${escapeHtml(item.right.label)}</strong><p>${escapeHtml(item.summary)}</p><small>${escapeHtml(item.left.summary)} ｜ ${escapeHtml(item.right.summary)}</small></div><div class="conflict-actions"><button class="button button-quiet target-button" data-type="${escapeHtml(item.left.type)}" data-id="${item.left.id}" type="button">查看左侧</button><button class="button button-quiet target-button" data-type="${escapeHtml(item.right.type)}" data-id="${item.right.id}" type="button">查看右侧</button><button class="button button-quiet contradiction-action" data-id="${item.id}" data-action="contextual" type="button">属于不同情境</button><button class="button button-quiet contradiction-action" data-id="${item.id}" data-action="false_positive" type="button">确认误报</button><button class="button button-danger contradiction-action" data-id="${item.id}" data-action="quarantine" type="button">隔离待查</button></div></article>`).join("")}</div></section>` : ""}${timeConflicts.length ? `<section class="conflict-group"><h4>时间顺序冲突 · ${timeConflicts.length}</h4><div class="conflict-card-list">${timeConflicts.map((item) => `<article class="conflict-card"><div><strong>${escapeHtml(item.earlier_title)} → ${escapeHtml(item.later_title)}</strong><p>${escapeHtml(item.reason || "该约束会造成时间循环。")}</p></div><div class="conflict-actions"><button class="button button-quiet target-button" data-type="event" data-id="${item.earlier_event_id}" type="button">查看前项</button><button class="button button-quiet target-button" data-type="event" data-id="${item.later_event_id}" type="button">查看后项</button><button class="button button-quiet time-conflict-action" data-id="${item.id}" data-action="reverse" type="button">反转顺序</button><button class="button button-danger time-conflict-action" data-id="${item.id}" data-action="reject" type="button">舍弃约束</button><button class="button button-quiet time-conflict-action" data-id="${item.id}" data-action="quarantine" type="button">隔离待查</button></div></article>`).join("")}</div></section>` : ""}${!autoConflictCount ? '<p class="conflict-complete">身份、事实和时间约束没有悬挂冲突。</p>' : ""}${resolvedHistory}</section>`;
  const reviewPanel = `<section class="quality-resolution"><div><h3>关系与地图门禁</h3><p>${modelReviewItems ? `还有 ${modelReviewItems} 项需要模型复审或人工补证。调用模型前会继续使用任务预算。` : "关系与地图项目已经闭环，不需要额外模型调用。"}</p></div>${modelReviewItems ? '<button id="quality-retry" class="button button-quiet" type="button">调用模型复审，可能计费</button>' : ""}</section>${unresolvedReviews.length ? `<div class="connectivity-resolution-list">${unresolvedReviews.map((item) => `<article><span><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.reason)} · 已扫描 ${item.scanned_segment_count} 个章节、${item.mention_count} 次提及</small></span><div><button class="button button-quiet target-button" data-type="entity" data-id="${item.entity_id}" type="button">查看人物</button><button class="button button-quiet connectivity-link" data-review="${item.id}" data-entity="${item.entity_id}" type="button">用原文补关系</button><button class="button button-danger connectivity-isolated" data-review="${item.id}" type="button">确认确实孤立</button></div></article>`).join("")}</div>` : ""}${unresolvedLocations.length ? `<div class="connectivity-resolution-list location-resolution-list">${unresolvedLocations.map((item) => `<article><span><strong>${escapeHtml(item.event_title)}</strong><small>${escapeHtml(item.reason)} · ${escapeHtml(chapterForSegment(item.event_first_segment))}</small></span><div><button class="button button-quiet target-button" data-type="event" data-id="${item.event_id}" type="button">查看剧情</button><button class="button button-primary location-link" data-event="${item.event_id}" type="button">用原文确认地点</button></div></article>`).join("")}</div>` : ""}`;
  const topologyMetrics = `<div class="quality-topology"><span>关系已连接 <strong>${quality.connectivity_reviewed_connected}</strong></span><span>确认孤立 <strong>${quality.connectivity_confirmed_isolated}</strong></span><span>关系待处理 <strong>${Number(quality.connectivity_pending || 0) + Number(quality.connectivity_ambiguous || 0)}</strong></span><span>地点明确 <strong>${quality.location_explicit_events}</strong></span><span>位置沿用 <strong>${quality.location_inherited_events}</strong></span><span>位置未解 <strong>${quality.location_unresolved_events}</strong></span><span>有效位置覆盖 <strong>${quality.effective_location_coverage_percent ?? "—"}%</strong></span></div>`;
  const benchmarkPanel = renderBenchmarkPanel();
  $("#view-panel").innerHTML = panelHead("质量检查", "片段覆盖、证据覆盖、主体准确率、关系完整性、地图完整性和每次模型费用分别计算。") + `<div class="quality-body">${metrics}${topologyMetrics}${benchmarkPanel}${conflictPanel}${reviewPanel}${jobHistory}${ledgerTable}${issues}</div>`;
  $("#quality-auto-close")?.addEventListener("click", autoCloseConflicts);
  $("#quality-retry")?.addEventListener("click", retryQualityChecks);
  $$(".connectivity-isolated").forEach((button) => button.addEventListener("click", () => confirmConnectivityIsolated(Number(button.dataset.review))));
  $$(".connectivity-link").forEach((button) => button.addEventListener("click", () => createManualConnectivityLink(Number(button.dataset.review), Number(button.dataset.entity))));
  $$(".location-link").forEach((button) => button.addEventListener("click", () => resolveManualEventLocation(Number(button.dataset.event))));
  $$(".contradiction-action").forEach((button) => button.addEventListener("click", () => resolveContradiction(Number(button.dataset.id), button.dataset.action)));
  $$(".time-conflict-action").forEach((button) => button.addEventListener("click", () => resolveTimeConflict(Number(button.dataset.id), button.dataset.action)));
  bindBenchmarkPanel();
  bindMergeReview();
  bindTargets();
}

async function autoCloseConflicts() {
  const button = $("#quality-auto-close");
  button.disabled = true;
  button.textContent = "正在使用本地规则处理…";
  try {
    const result = await api(`/api/books/${state.bookId}/conflicts/auto-resolve`, { method: "POST" });
    const summary = result.resolution;
    toast(`本地处理完成：身份 ${summary.identity_merged + summary.identity_separated} 项，事实冲突 ${summary.contradictions_quarantined} 项，时间约束 ${summary.time_constraints_rejected} 项；费用 $0。`);
    await loadOverview(Number($("#progress-slider").value), true);
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
    button.textContent = "免费自动处理未闭环冲突";
  }
}

async function retryQualityChecks() {
  const button = $("#quality-retry");
  button.disabled = true;
  button.textContent = "正在复审未闭环项目…";
  try {
    const result = await api(`/api/books/${state.bookId}/quality/retry`, { method: "POST" });
    toast(result.status === "completed" ? "质量门禁已经通过。" : "自动复审已完成，剩余歧义可以人工解决。");
    await loadOverview(Number($("#progress-slider").value), true);
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
    button.textContent = "自动解决剩余问题";
  }
}

async function resolveContradiction(id, action) {
  const defaults = {
    contextual: "两条记录分别适用于不同时间、地点、身份视角或条件。",
    false_positive: "两条记录可以同时成立，这一冲突属于自动检查误报。",
    quarantine: "当前证据不足以裁决，先从正式结论中隔离并保留原始证据。",
  };
  const reason = window.prompt("请用陈述句记录这次判断依据。", defaults[action]);
  if (!reason) return;
  try {
    await api(`/api/contradictions/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, reason }),
    });
    toast("事实冲突已经关闭，原始记录和证据仍然保留。");
    await loadOverview(Number($("#progress-slider").value), true);
  } catch (error) {
    toast(error.message, true);
  }
}

async function resolveTimeConflict(id, action) {
  const defaults = {
    reverse: "原文证据表明两件事件的先后方向应当反转。",
    reject: "该顺序约束会形成循环，舍弃约束并保留两件剧情事件。",
    quarantine: "当前证据不足以确定先后，暂时隔离该约束。",
  };
  const reason = window.prompt("请用陈述句记录这次判断依据。", defaults[action]);
  if (!reason) return;
  try {
    await api(`/api/time-conflicts/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, reason }),
    });
    toast(action === "reverse" ? "时间顺序已经反转并重新验算。" : "时间约束已经关闭，剧情事件仍然保留。");
    await loadOverview(Number($("#progress-slider").value), true);
  } catch (error) {
    toast(error.message, true);
  }
}

async function confirmConnectivityIsolated(reviewId) {
  const reason = window.prompt("请用一句陈述说明为什么确认该人物或势力没有可建立的关系。", "已核对全部提及窗口，没有发现明确关系。");
  if (!reason) return;
  try {
    await api(`/api/connectivity-reviews/${reviewId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "confirmed_isolated", reason }),
    });
    toast("该节点已经标记为人工确认孤立。");
    await loadOverview(Number($("#progress-slider").value), true);
  } catch (error) {
    toast(error.message, true);
  }
}

async function createManualConnectivityLink(reviewId, sourceEntityId) {
  const people = state.overview.entities.filter((item) => ["person", "faction"].includes(item.kind) && Number(item.id) !== sourceEntityId);
  const targetName = window.prompt(`输入要连接的人物或势力名称。\n例如：${people.slice(0, 8).map((item) => item.name).join("、")}`);
  if (!targetName) return;
  const target = people.find((item) => item.name === targetName.trim());
  if (!target) {
    toast("没有找到完全同名的人物或势力。", true);
    return;
  }
  const predicate = window.prompt("输入简短关系，例如父亲、效忠、追捕或盟友。");
  const segmentNumber = Number(window.prompt(`输入原文章节序号，范围 1–${state.overview.segments.length}。`));
  const segment = state.overview.segments[segmentNumber - 1];
  const evidenceQuote = window.prompt("粘贴该章节中的逐字连续原文，引文必须能直接证明这条关系。");
  const summary = window.prompt("用一句陈述写清关系如何成立以及适用条件。");
  if (!predicate || !segment || !evidenceQuote || !summary) {
    toast("人工补关系需要对象、关系、章节、逐字引文和说明。", true);
    return;
  }
  try {
    await api(`/api/connectivity-reviews/${reviewId}/relation`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_entity_id: target.id, predicate, summary, segment_id: segment.id, evidence_quote: evidenceQuote }),
    });
    toast("关系已经建立，并通过逐字原文校验。");
    await loadOverview(Number($("#progress-slider").value), true);
  } catch (error) {
    toast(error.message, true);
  }
}

async function resolveManualEventLocation(eventId) {
  const places = state.overview.entities.filter((item) => item.kind === "place");
  const locationName = window.prompt(`输入剧情发生地点的完整名称。\n例如：${places.slice(0, 10).map((item) => item.name).join("、")}`);
  if (!locationName) return;
  const location = places.find((item) => item.name === locationName.trim());
  if (!location) {
    toast("没有找到完全同名的地点。", true);
    return;
  }
  const segmentNumber = Number(window.prompt(`输入原文章节序号，范围 1–${state.overview.segments.length}。`));
  const segment = state.overview.segments[segmentNumber - 1];
  const evidenceQuote = window.prompt("粘贴该章节中能够确认地点的逐字连续原文。");
  if (!segment || !evidenceQuote) {
    toast("确认地点需要原文章节和逐字引文。", true);
    return;
  }
  try {
    await api(`/api/event-location-reviews/${eventId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ location_entity_id: location.id, segment_id: segment.id, evidence_quote: evidenceQuote }),
    });
    toast("剧情地点已经确认，并通过逐字原文校验。");
    await loadOverview(Number($("#progress-slider").value), true);
  } catch (error) {
    toast(error.message, true);
  }
}

function findTarget(type, id) {
  const collections = {
    entity: state.overview.entities,
    claim: state.overview.claims,
    place_relation: state.overview.geography_relations,
    event: state.overview.events,
    world_note: state.overview.world_notes,
    entry: state.overview.entries,
  };
  return collections[type]?.find((item) => Number(item.id) === Number(id));
}

function bindTargets() {
  $$(".target-button, .graph-node, .edge-target").forEach((element) => {
    const activate = () => openInspector(element.dataset.type, Number(element.dataset.id));
    element.addEventListener("click", activate);
    element.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        activate();
      }
    });
  });
}

async function openInspector(type, id) {
  const item = findTarget(type, id);
  if (!item) return;
  // 每次打开都登记唯一目标；较慢的旧证据请求返回时不得覆盖用户后来选择的新行程步。
  state.inspectorTarget = { type, id: Number(id) };
  const requestSerial = ++state.inspectorRequestSerial;
  const title = item.name || item.title || `${item.source_name} → ${item.target_name}`;
  $("#inspector-title").textContent = title;
  $("#inspector-body").innerHTML = '<div class="loading">正在读取原文证据…</div>';
  $("#inspector").classList.add("open");
  $(".app-shell").classList.add("inspector-open");
  $("#inspector").setAttribute("aria-hidden", "false");
  $("#inspector").removeAttribute("inert");
  $("#scrim").hidden = !window.matchMedia("(max-width: 1100px)").matches;
  try {
    const evidence = await api(`/api/evidence/${type}/${id}`);
    if (requestSerial !== state.inspectorRequestSerial
        || state.inspectorTarget?.type !== type
        || Number(state.inspectorTarget?.id) !== Number(id)) return;
    const summary = type === "event" ? eventNarrativeText(item) : item.summary || "当前条目没有补充说明。";
    const aliases = item.aliases?.length ? item.aliases.join("、") : "—";
    const details = type === "entity"
      ? [["类别", categoryLabels[item.kind] || item.kind], ["别名", aliases], ["首次出现", chapterForSegment(item.first_segment)]]
      : type === "claim"
        ? [["关系", `${item.source_name} —${item.predicate}→ ${item.target_name}`], ["首次确认", chapterForSegment(item.first_segment)], ["审核状态", item.status], ["置信度", `${Math.round(item.confidence * 100)}%`]]
        : type === "event"
          ? [["故事时间", item.temporal_value || "未知"], ["原文章节", chapterForSegment(item.first_segment)], ["叙事层级", item.narrative_phase || "unknown"], ["地点", item.location_name || "未说明"], ["交通", transportLabels[item.transport] || "未说明"]]
          : type === "place_relation"
            ? [["方位关系", `${item.source_name} —${item.relative_position}→ ${item.target_name}`], ["首次确认", chapterForSegment(item.first_segment)], ["置信度", `${Math.round(item.confidence * 100)}%`]]
            : [["类别", categoryLabels[item.category] || item.category], ["首次出现", chapterForSegment(item.first_segment)], ["置信度", `${Math.round(item.confidence * 100)}%`]];
    const detailRows = details.map(([key, value]) => `<div class="detail-row"><span>${escapeHtml(key)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
    const evidenceCards = evidence.length ? evidence.map((source) => `<button class="evidence-card source-button" data-segment="${source.segment_id}" data-quote="${escapeHtml(source.quote)}" type="button"><blockquote>“${escapeHtml(source.quote)}”</blockquote><small>${escapeHtml(source.chapter_title)} · 点击回到原文</small></button>`).join("") : '<p class="detail-summary">当前记录缺少原文证据。它不会计入证据覆盖率。</p>';
    const lineageItems = evidence.map((source) => source.lineage).filter((value) => value?.manifest || value?.model_call);
    const lineageKeys = new Set();
    const lineage = lineageItems.filter((value) => {
      const key = `${value.manifest?.id || "legacy"}:${value.model_call?.id || "local"}`;
      if (lineageKeys.has(key)) return false;
      lineageKeys.add(key);
      return true;
    });
    const lineagePanel = lineage.length ? `<details class="lineage-panel"><summary>查看这条结果的模型、提示词、成本和版本</summary>${lineage.map((value) => { const manifest = value.manifest; const call = value.model_call; return `<article><strong>${escapeHtml(call?.provider || manifest?.provider || "本地规则")} · ${escapeHtml(call?.model || manifest?.model || "未记录")}</strong><span>运行 #${manifest?.id || "旧记录"} · 调用 #${call?.id || "本地整理"} · ${escapeHtml(call?.status || manifest?.status || value.trace_status)}</span><span>提示词 ${escapeHtml((call?.prompt_hash || manifest?.prompt_hash || "旧版未记录").slice(0, 16))} · 合同 ${escapeHtml(manifest?.contract_version || "旧版未记录")}</span><span>输入 ${Number(call?.input_tokens || 0).toLocaleString()} · 输出 ${Number(call?.output_tokens || 0).toLocaleString()} · ${call?.estimated_cost_usd == null ? "订阅、本地或旧记录不换算美元" : escapeHtml(formatCost(call))}</span></article>`; }).join("")}</details>` : '<p class="lineage-legacy">这是升级前生成或完全由本地规则整理的记录。原文证据仍然有效，但旧版本没有完整运行清单。</p>';
    const review = type === "claim" ? `<div class="review-actions"><button class="button button-quiet review-button" data-status="accepted" type="button">确认关系</button><button class="button button-danger review-button" data-status="rejected" type="button">标记错误</button></div>` : "";
    const canRegenerate = ["world_note", "entry"].includes(type);
    const edit = `<div class="record-actions"><button class="button button-quiet edit-record" type="button">人工编辑</button>${canRegenerate ? '<button class="button button-quiet regenerate-record" type="button">按要求二次生成</button>' : ""}</div><div id="record-editor" class="record-editor" hidden><label for="record-summary-input">修正后的说明</label><textarea id="record-summary-input">${escapeHtml(summary)}</textarea><button class="button button-primary save-record-edit" type="button">保存人工版本</button></div>${canRegenerate ? `<div id="draft-editor" class="draft-editor" hidden><label for="draft-instruction">使用陈述句写明整理任务</label><textarea id="draft-instruction" placeholder="补充证据中已经明确的适用条件、限制和后果，删除重复表述。"></textarea><label for="draft-budget">本次草稿金额上限，美元</label><input id="draft-budget" type="number" min="0" step="0.01" value="0.05"><button class="button button-primary create-record-draft" type="button">生成候选版本</button><div id="draft-preview"></div></div>` : ""}`;
    const mapAction = type === "event" && item.location_entity_id ? `<button class="button button-quiet full map-jump" data-location="${item.location_entity_id}" type="button">在地图中查看地点</button>` : "";
    // 地图中的“下一步”必须沿主线行程前进；其他页面仍按完整编年事件前进。
    const journey = storyMapSteps();
    const currentJourneyEvent = journey[state.mapStep];
    const journeyIndex = type === "event" && state.view === "map"
      ? Number(currentJourneyEvent?.id) === Number(item.id)
        ? state.mapStep
        : journey.findIndex((event) => Number(event.id) === Number(item.id))
      : -1;
    const eventIndex = type === "event"
      ? state.overview.events.findIndex((event) => Number(event.id) === Number(item.id))
      : -1;
    const nextEvent = journeyIndex >= 0 ? journey[journeyIndex + 1] : eventIndex >= 0 ? state.overview.events[eventIndex + 1] : null;
    const nextAction = nextEvent ? `<button class="button button-primary full next-event" data-id="${nextEvent.id}" type="button">下一步：${escapeHtml(nextEvent.title)}</button>` : "";
    const placeEvents = type === "entity" && item.kind === "place" ? state.overview.events.filter((event) => event.location_entity_id === item.id) : [];
    const placeHistory = placeEvents.length ? `<h3 class="evidence-title">此地发生的历史</h3>${placeEvents.map((event) => `<button class="evidence-card place-event" data-id="${event.id}" type="button"><strong>${escapeHtml(event.temporal_value || "时间未知")} · ${escapeHtml(event.title)}</strong><small>${escapeHtml(eventNarrativeText(event))}</small></button>`).join("")}` : "";
    $("#inspector-body").innerHTML = `<p class="detail-summary">${escapeHtml(summary)}</p><div class="detail-list">${detailRows}</div>${edit}${mapAction}${nextAction}${placeHistory}<h3 class="evidence-title">逐字原文证据</h3>${evidenceCards}<h3 class="evidence-title">生成溯源</h3>${lineagePanel}${review}`;
    $$(".source-button").forEach((button) => button.addEventListener("click", () => openSource(Number(button.dataset.segment), button.dataset.quote)));
    $$(".review-button").forEach((button) => button.addEventListener("click", () => reviewClaim(id, button.dataset.status)));
    $(".edit-record")?.addEventListener("click", () => { $("#record-editor").hidden = !$("#record-editor").hidden; });
    $(".save-record-edit")?.addEventListener("click", () => editRecord(type, id, summary));
    $(".regenerate-record")?.addEventListener("click", () => { $("#draft-editor").hidden = !$("#draft-editor").hidden; });
    $(".create-record-draft")?.addEventListener("click", () => generateRecordDraft(type, id));
    $(".next-event")?.addEventListener("click", (event) => {
      const nextId = Number(event.currentTarget.dataset.id);
      const nextJourneyStep = state.view === "map"
        ? storyMapSteps().findIndex((journeyEvent) => Number(journeyEvent.id) === nextId)
        : -1;
      if (nextJourneyStep >= 0) setMapStep(nextJourneyStep);
      else openInspector("event", nextId);
    });
    $$(".place-event").forEach((button) => button.addEventListener("click", () => openInspector("event", Number(button.dataset.id))));
    $(".map-jump")?.addEventListener("click", (event) => {
      state.activeLocation = Number(event.currentTarget.dataset.location);
      const journeyStep = storyMapSteps().findIndex((journeyEvent) => Number(journeyEvent.id) === Number(item.id));
      if (journeyStep >= 0) state.mapStep = journeyStep;
      state.view = "map";
      closeInspector();
      renderView();
    });
  } catch (error) {
    if (requestSerial !== state.inspectorRequestSerial) return;
    $("#inspector-body").innerHTML = `<p class="detail-summary">${escapeHtml(error.message)}</p>`;
  }
}

async function editRecord(type, id, currentSummary) {
  const newValue = $("#record-summary-input")?.value.trim();
  if (!newValue || newValue === currentSummary) return;
  try {
    await api(`/api/records/${type}/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ field_name: "summary", new_value: newValue, reason: "用户在详情页修正" }),
    });
    closeInspector();
    await loadOverview(Number($("#progress-slider").value));
    toast("修正已经保存，旧内容保留在修改记录中。");
  } catch (error) {
    toast(error.message, true);
  }
}

async function generateRecordDraft(type, id) {
  const instruction = $("#draft-instruction")?.value.trim();
  const preview = $("#draft-preview");
  if (!instruction || instruction.length < 6) {
    preview.innerHTML = '<p class="detail-summary">整理任务至少写六个字，并明确要修改什么。</p>';
    return;
  }
  if (/[?？]/.test(instruction)) {
    preview.innerHTML = '<p class="detail-summary">请把问句改成陈述式任务，例如“补充这条设定的限制和后果”。</p>';
    return;
  }
  if (!/(补充|改写|整理|说明|突出|合并|修正|扩写|精简|生成)/.test(instruction)) {
    preview.innerHTML = '<p class="detail-summary">请写明补充、改写、整理或修正等具体任务。</p>';
    return;
  }
  preview.innerHTML = '<div class="loading">正在生成证据受限草稿…</div>';
  try {
    const draft = await api(`/api/records/${type}/${id}/drafts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: $("#provider-select").value,
        instruction,
        max_cost_usd: Math.max(0, Number($("#draft-budget").value || 0)),
      }),
    });
    preview.innerHTML = `<div class="draft-preview"><strong>${escapeHtml(draft.title)}</strong><span>${escapeHtml(draft.summary)}</span><small>${escapeHtml(categoryLabels[draft.category] || draft.category)} · ${draft.evidence_quotes.length} 条证据 · ${escapeHtml(formatCost(draft))}</small><button class="button button-primary apply-record-draft" data-id="${draft.id}" type="button">确认应用这个版本</button></div>`;
    $(".apply-record-draft")?.addEventListener("click", async (event) => {
      try {
        await api(`/api/record-drafts/${Number(event.currentTarget.dataset.id)}/apply`, { method: "POST" });
        closeInspector();
        await loadOverview(Number($("#progress-slider").value));
        toast("候选版本已经应用，修改前后的内容都已保存。")
      } catch (error) {
        toast(error.message, true);
      }
    });
  } catch (error) {
    preview.innerHTML = `<p class="detail-summary">${escapeHtml(error.message)}</p>`;
  }
}

async function reviewClaim(id, status) {
  try {
    await api(`/api/claims/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status, reason: "网页人工审核" }),
    });
    toast(status === "accepted" ? "关系已确认，审核记录已保存。" : "关系已标记错误，派生视图将隐藏它。");
    closeInspector();
    await loadOverview(Number($("#progress-slider").value));
  } catch (error) {
    toast(error.message, true);
  }
}

async function openSource(segmentId, quote) {
  try {
    const source = await api(`/api/segments/${segmentId}`);
    $("#source-title").textContent = source.chapter_title;
    $("#source-meta").textContent = `${source.anchor} · 字符 ${source.char_start}–${source.char_end}`;
    const escapedText = escapeHtml(source.text);
    const escapedQuote = escapeHtml(quote);
    $("#source-text").innerHTML = escapedQuote && escapedText.includes(escapedQuote)
      ? escapedText.replace(escapedQuote, `<mark>${escapedQuote}</mark>`)
      : escapedText;
    $("#source-dialog").showModal();
  } catch (error) {
    toast(error.message, true);
  }
}

function closeInspector() {
  // 关闭后解除与行程的联动，并让尚未返回的证据请求失效。
  state.inspectorTarget = null;
  state.inspectorRequestSerial += 1;
  $("#inspector").classList.remove("open");
  $(".app-shell").classList.remove("inspector-open");
  $("#inspector").setAttribute("aria-hidden", "true");
  $("#inspector").setAttribute("inert", "");
  $("#scrim").hidden = true;
}

function folderOptions(selectedId = null, excludedId = null) {
  const folders = state.folders.filter((folder) => Number(folder.id) !== Number(excludedId));
  return [`<option value="root" ${selectedId === null ? "selected" : ""}>根目录</option>`, ...folders.map((folder) =>
    `<option value="${folder.id}" ${Number(selectedId) === Number(folder.id) ? "selected" : ""}>${escapeHtml(folder.name)}</option>`
  )].join("");
}

async function refreshLibraryData() {
  const selected = state.bookId;
  [state.books, state.folders] = await Promise.all([api("/api/books"), api("/api/library/folders")]);
  renderBookOptions();
  if (selected && state.books.some((book) => Number(book.id) === Number(selected))) {
    $("#book-select").value = String(selected);
  }
}

function renderLibraryManager() {
  $("#new-folder-parent").innerHTML = folderOptions();
  const folders = state.folders.length ? state.folders.map((folder) => `<div class="library-folder-row" data-folder="${folder.id}"><input class="folder-name-edit" value="${escapeHtml(folder.name)}" aria-label="文件夹名称"><select class="folder-parent-edit" aria-label="上级目录">${folderOptions(folder.parent_id, folder.id)}</select><button class="button button-quiet save-folder" type="button">保存</button><button class="button button-danger delete-folder" type="button">删除</button></div>`).join("") : '<p class="merge-complete">还没有文件夹，书籍目前都在根目录。</p>';
  const books = state.books.length ? state.books.map((book) => `<div class="library-book-row" data-book="${book.id}"><input class="book-title-edit" value="${escapeHtml(book.title)}" aria-label="书名"><input class="book-author-edit" value="${escapeHtml(book.author || "")}" placeholder="作者" aria-label="作者"><select class="book-folder-edit" aria-label="所在文件夹">${folderOptions(book.folder_id)}</select><button class="button button-quiet open-book" type="button">打开</button><button class="button button-quiet save-book" type="button">保存</button><button class="button button-danger delete-book-row" type="button">删除</button></div>`).join("") : '<p class="merge-complete">书库中还没有书籍。</p>';
  $("#library-manager-body").innerHTML = `<section class="library-manager-section"><h3>文件夹</h3>${folders}</section><section class="library-manager-section"><h3>书籍</h3>${books}</section>`;
  $$(".save-folder").forEach((button) => button.addEventListener("click", () => saveFolder(button.closest(".library-folder-row"))));
  $$(".delete-folder").forEach((button) => button.addEventListener("click", () => deleteFolder(button.closest(".library-folder-row"))));
  $$(".open-book").forEach((button) => button.addEventListener("click", () => openManagedBook(button.closest(".library-book-row"))));
  $$(".save-book").forEach((button) => button.addEventListener("click", () => saveManagedBook(button.closest(".library-book-row"))));
  $$(".delete-book-row").forEach((button) => button.addEventListener("click", () => deleteManagedBook(button.closest(".library-book-row"))));
}

async function openLibraryManager() {
  try {
    await refreshLibraryData();
    renderLibraryManager();
    $("#library-dialog").showModal();
  } catch (error) {
    toast(error.message, true);
  }
}

async function createFolder() {
  const name = $("#new-folder-name").value.trim();
  if (!name) return;
  const parentValue = $("#new-folder-parent").value;
  try {
    await api("/api/library/folders", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, parent_id: parentValue === "root" ? null : Number(parentValue) }),
    });
    $("#new-folder-name").value = "";
    await refreshLibraryData();
    renderLibraryManager();
  } catch (error) { toast(error.message, true); }
}

async function saveFolder(row) {
  const id = Number(row.dataset.folder);
  const parentValue = row.querySelector(".folder-parent-edit").value;
  try {
    await api(`/api/library/folders/${id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: row.querySelector(".folder-name-edit").value.trim(), parent_id: parentValue === "root" ? null : Number(parentValue) }),
    });
    await refreshLibraryData();
    renderLibraryManager();
    toast("文件夹已经保存。");
  } catch (error) { toast(error.message, true); }
}

async function deleteFolder(row) {
  const id = Number(row.dataset.folder);
  if (!window.confirm("删除这个文件夹吗？其中的书籍和子文件夹会移回根目录。")) return;
  try {
    await api(`/api/library/folders/${id}`, { method: "DELETE" });
    await refreshLibraryData();
    renderLibraryManager();
  } catch (error) { toast(error.message, true); }
}

async function openManagedBook(row) {
  state.bookId = Number(row.dataset.book);
  resetMapStateForBook();
  $("#book-select").value = String(state.bookId);
  $("#library-dialog").close();
  await loadOverview();
}

async function saveManagedBook(row) {
  const id = Number(row.dataset.book);
  const folderValue = row.querySelector(".book-folder-edit").value;
  try {
    await api(`/api/books/${id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: row.querySelector(".book-title-edit").value.trim(),
        author: row.querySelector(".book-author-edit").value.trim(),
        folder_id: folderValue === "root" ? null : Number(folderValue),
        move_to_root: folderValue === "root",
      }),
    });
    await refreshLibraryData();
    renderLibraryManager();
    if (Number(state.bookId) === id) await loadOverview(null, true);
    toast("书籍信息已经保存。");
  } catch (error) { toast(error.message, true); }
}

async function deleteManagedBook(row) {
  const id = Number(row.dataset.book);
  const book = state.books.find((item) => Number(item.id) === id);
  if (!window.confirm(`确定删除《${book?.title || "这本书"}》及其分析结果吗？`)) return;
  try {
    await api(`/api/books/${id}`, { method: "DELETE" });
    if (Number(state.bookId) === id) state.bookId = null;
    await refreshLibraryData();
    renderLibraryManager();
    if (!state.bookId && state.books.length) {
      state.bookId = Number(state.books[0].id);
      $("#book-select").value = String(state.bookId);
      await loadOverview();
    }
  } catch (error) { toast(error.message, true); }
}

async function importFile(file) {
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  const currentBook = state.books.find((book) => Number(book.id) === Number(state.bookId));
  if (currentBook?.folder_id) form.append("folder_id", String(currentBook.folder_id));
  const button = $("#upload-button");
  button.disabled = true;
  button.textContent = "正在安全导入…";
  try {
    const result = await api("/api/books/import", { method: "POST", body: form });
    await refreshLibraryData();
    state.bookId = Number(result.id);
    state.mapStep = 0;
    state.mapViewport = null;
    $("#book-select").value = String(state.bookId);
    await loadOverview();
    toast(`已导入《${result.title}》，共 ${result.segments} 个证据片段。`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.innerHTML = '<span aria-hidden="true">＋</span> 导入小说';
    $("#file-input").value = "";
  }
}

async function openUpdateDialog() {
  if (!state.bookId) return;
  $("#book-update-file").value = "";
  $("#update-result").innerHTML = "";
  $("#update-dialog").showModal();
  await loadUpdateHistory();
}

function renderUpdateResult(result) {
  const conflicts = result.conflicts || [];
  if (result.status === "needs_review") {
    const cards = conflicts.map((item) => `<article class="update-conflict"><strong>第 ${Number(item.ordinal) + 1} 个片段 · ${escapeHtml(item.kind)}</strong><small>${escapeHtml(item.old_title)} → ${escapeHtml(item.new_title)}</small><p>当前版本：${escapeHtml(item.old_excerpt || "无")}</p><p>上传版本：${escapeHtml(item.new_excerpt || "无")}</p></article>`).join("");
    $("#update-result").innerHTML = `<div class="update-summary warning">发现 ${conflicts.length} 处旧内容变化。系统没有覆盖当前书籍，下面已经列出全部差异。</div><div class="update-conflicts">${cards}</div><div class="update-actions"><button class="button button-quiet resolve-update" data-id="${result.id}" data-action="keep_current" type="button">保留当前版本</button><button class="button button-quiet resolve-update" data-id="${result.id}" data-action="import_as_new" type="button">把上传版本另存为新书</button><button class="button button-primary resolve-update" data-id="${result.id}" data-action="auto" type="button">系统保守处理</button></div>`;
  } else {
    const added = Number(result.added_segment_count || 0);
    $("#update-result").innerHTML = `<div class="update-summary">${escapeHtml(result.message)}${added ? ` 新增 ${added} 个片段，分析起点为第 ${Number(result.start_segment) + 1} 个片段。` : ""}</div>${added ? `<button class="button button-primary analyze-added" data-start="${result.start_segment}" type="button">只分析新增内容</button>` : ""}`;
  }
  $$(".resolve-update").forEach((button) => button.addEventListener("click", () => resolveUpdate(Number(button.dataset.id), button.dataset.action)));
  $(".analyze-added")?.addEventListener("click", (event) => {
    $("#update-dialog").close();
    analyze(Number(event.currentTarget.dataset.start));
  });
}

async function submitBookUpdate() {
  const file = $("#book-update-file").files[0];
  if (!file) {
    toast("请先选择更新文件。", true);
    return;
  }
  const button = $("#preview-update-button");
  const form = new FormData();
  form.append("file", file);
  form.append("mode", $("#book-update-mode").value);
  button.disabled = true;
  button.textContent = "正在比较全部章节…";
  try {
    const result = await api(`/api/books/${state.bookId}/updates`, { method: "POST", body: form });
    renderUpdateResult(result);
    await refreshLibraryData();
    await loadOverview(null, true);
    await loadUpdateHistory();
  } catch (error) {
    $("#update-result").innerHTML = `<div class="update-summary warning">${escapeHtml(error.message)}</div>`;
  } finally {
    button.disabled = false;
    button.textContent = "比较并更新";
  }
}

async function resolveUpdate(updateId, action) {
  try {
    const result = await api(`/api/book-updates/${updateId}/resolve`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }),
    });
    await refreshLibraryData();
    if (action !== "keep_current") {
      state.bookId = Number(result.book_id);
      $("#book-select").value = String(state.bookId);
      state.mapStep = 0;
      state.mapViewport = null;
      await loadOverview();
      $("#update-dialog").close();
      toast("冲突版已经另存，当前版本完整保留。");
    } else {
      $("#update-result").innerHTML = '<div class="update-summary">本次更新已结束，当前版本和既有分析保持不变。</div>';
      await loadUpdateHistory();
    }
  } catch (error) { toast(error.message, true); }
}

async function loadUpdateHistory() {
  try {
    const updates = await api(`/api/books/${state.bookId}/updates`);
    $("#update-history").innerHTML = updates.length ? `<section class="library-manager-section"><h3>更新记录</h3>${updates.map((item) => `<div class="update-conflict"><strong>#${item.id} · ${escapeHtml(item.status)} · ${Number(item.added_segment_count || 0)} 个新增片段</strong><small>${escapeHtml(item.filename)} · ${Number(item.conflict_count || 0)} 处差异${item.resolution ? ` · ${escapeHtml(item.resolution)}` : ""}</small></div>`).join("")}</section>` : "";
  } catch (error) {
    $("#update-history").innerHTML = `<div class="update-summary warning">${escapeHtml(error.message)}</div>`;
  }
}

async function analyze(startSegment = 0) {
  state.analysisStartSegment = Number(startSegment) || 0;
  state.budgetJobId = null;
  $("#analysis-reestimate").hidden = false;
  $("#analysis-review-mode").hidden = false;
  document.querySelector('label[for="analysis-review-mode"]')?.removeAttribute("hidden");
  $("#analysis-start").textContent = "启动自动分析";
  $("#analysis-dialog").showModal();
  await refreshAnalysisEstimate();
}

async function refreshAnalysisEstimate() {
  const provider = $("#provider-select").value;
  const reviewMode = $("#analysis-review-mode").value;
  const estimateBox = $("#analysis-estimate");
  estimateBox.textContent = "正在计算保守预估…";
  $("#analysis-start").disabled = true;
  try {
    const estimate = await api(`/api/books/${state.bookId}/jobs/estimate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, start_segment: state.analysisStartSegment, end_segment: null, review_mode: reviewMode }),
    });
    state.analysisEstimate = estimate;
    renderAnalysisEstimate();
  } catch (error) {
    state.analysisEstimate = null;
    estimateBox.textContent = error.message;
    estimateBox.classList.add("over");
  }
}

function renderAnalysisEstimate() {
  const estimate = state.analysisEstimate;
  if (!estimate) return;
  const budget = Math.max(0, Number($("#analysis-budget").value || 0));
  const amount = estimate.estimated_cost_usd;
  const over = amount !== null && Number(amount) > budget;
  const estimateBox = $("#analysis-estimate");
  const amountLabel = amount === null ? "当前价格不可复算" : `$${Number(amount).toFixed(Number(amount) < 0.01 ? 6 : 4)}`;
  estimateBox.classList.toggle("over", false);
  estimateBox.innerHTML = `<strong>保守预估 ${escapeHtml(amountLabel)}</strong><br>${Number(estimate.segment_count).toLocaleString()} 个片段 · 输入最多约 ${Number(estimate.estimated_input_tokens).toLocaleString()} · 输出最多约 ${Number(estimate.estimated_output_tokens).toLocaleString()} 令牌<br>${over ? `初始参考为 $${budget.toFixed(2)}，任务会自动扩展到覆盖实际分析所需的范围。` : "系统会根据真实用量继续校准，不会因保守预估提前停止。"}`;
  $("#analysis-start").disabled = false;
}

async function startAnalysis() {
  const button = $("#analysis-start");
  const estimate = state.analysisEstimate;
  if (state.budgetJobId) {
    const job = state.overview.analysis_jobs?.find((item) => Number(item.id) === state.budgetJobId)
      || await api(`/api/jobs/${state.budgetJobId}`);
    const budget = Math.max(Number(job.estimated_cost_usd || 0), Number($("#analysis-budget").value || 0));
    try {
      const updated = await api(`/api/jobs/${state.budgetJobId}/budget`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          max_cost_usd: budget,
          max_input_tokens: Math.max(Number(job.max_input_tokens || 0) * 2, Number(job.input_tokens || 0) + 100000),
          max_output_tokens: Math.max(Number(job.max_output_tokens || 0) * 2, Number(job.output_tokens || 0) + 30000),
          budget_mode: "adaptive",
        }),
      });
      $("#analysis-dialog").close();
      renderJob(updated);
      toast("自动适配范围已保存，点击继续即可恢复任务。")
    } catch (error) {
      toast(error.message, true);
    }
    return;
  }
  if (!estimate) return;
  const provider = $("#provider-select").value;
  const budget = Math.max(0, Number($("#analysis-budget").value || 0));
  const reviewMode = $("#analysis-review-mode").value;
  button.disabled = true;
  button.textContent = "正在建立任务…";
  try {
    const job = await api(`/api/books/${state.bookId}/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider,
        start_segment: state.analysisStartSegment,
        end_segment: null,
        max_retries: 3,
        reanalyze: false,
        max_cost_usd: budget,
        max_input_tokens: Math.max(1000, Math.ceil(Number(estimate.estimated_input_tokens) * 1.12)),
        max_output_tokens: Math.max(1000, Math.ceil(Number(estimate.estimated_output_tokens) * 1.12)),
        budget_mode: "adaptive",
        review_mode: reviewMode,
      }),
    });
    $("#analysis-dialog").close();
    state.activeJobId = job.id;
    renderJob(job);
    if (job.status === "completed" && Number(job.total_segments) === 0) {
      toast("整本书已经分析完成，没有重复调用模型。");
    } else {
      toast("整本书分析已经开始，可以关闭页面或稍后继续查看。");
      scheduleJobPoll();
    }
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "启动自动分析";
  }
}

async function searchBook() {
  const query = $("#global-search").value.trim();
  if (!query) return;
  state.searchQuery = query;
  $("#search-title").textContent = `“${query}”的搜索结果`;
  $("#search-results").innerHTML = '<div class="loading">正在搜索全书…</div>';
  $("#search-dialog").showModal();
  try {
    const visible = Number($("#progress-slider").value);
    const results = await api(`/api/books/${state.bookId}/search?q=${encodeURIComponent(query)}&through_segment=${visible}`);
    $("#search-results").innerHTML = results.length ? results.map((result) => `<button class="search-result" data-type="${escapeHtml(result.target_type)}" data-id="${result.target_id}" type="button"><strong>${escapeHtml(result.title)}</strong><span>${escapeHtml(result.snippet)}</span></button>`).join("") : emptyState("没有找到匹配内容", "可以更换人物别名、地点或原文词句。");
    $$(".search-result").forEach((button) => button.addEventListener("click", () => {
      $("#search-dialog").close();
      if (button.dataset.type === "segment") {
        openSource(Number(button.dataset.id), state.searchQuery);
      } else {
        openInspector(button.dataset.type, Number(button.dataset.id));
      }
    }));
  } catch (error) {
    $("#search-results").innerHTML = emptyState("搜索失败", error.message);
  }
}

function download(path) {
  const link = document.createElement("a");
  link.href = path;
  link.hidden = true;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

async function deleteCurrentBook() {
  const title = state.overview.book.title;
  if (!window.confirm(`确定删除《${title}》吗？这会删除原文、分析结果和修改记录。建议先备份。`)) return;
  try {
    await api(`/api/books/${state.bookId}`, { method: "DELETE" });
    await refreshLibraryData();
    if (state.books.length) {
      state.bookId = Number(state.books[0].id);
      state.mapStep = 0;
      state.mapViewport = null;
      $("#book-select").value = String(state.bookId);
      await loadOverview();
    } else {
      state.bookId = null;
      $("#view-panel").innerHTML = emptyState("书库是空的", "导入一本小说后即可开始分析。");
    }
    toast(`《${title}》已经从本机数据库删除。`);
  } catch (error) {
    toast(error.message, true);
  }
}

async function saveProviderKey() {
  const provider = $("#key-provider").value;
  const apiKey = $("#provider-key").value.trim();
  if (!apiKey) {
    toast("请先粘贴开放平台密钥。", true);
    return;
  }
  try {
    await api("/api/settings/provider-key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, api_key: apiKey }),
    });
    $("#provider-key").value = "";
    state.providers = await api("/api/providers");
    renderProviderOptions();
    $("#key-dialog").close();
    toast("模型密钥已由 Windows 当前账户加密保存。");
  } catch (error) {
    toast(error.message, true);
  }
}

async function deleteProviderKey() {
  const provider = $("#key-provider").value;
  if (!window.confirm("确定删除这个平台保存在本机的密钥吗？")) return;
  try {
    await api(`/api/settings/provider-key/${provider}`, { method: "DELETE" });
    state.providers = await api("/api/providers");
    renderProviderOptions();
    $("#key-dialog").close();
    toast("本机保存的模型密钥已经删除。");
  } catch (error) {
    toast(error.message, true);
  }
}

// 绑定全局操作，视图内部操作会在每次渲染后重新绑定。
$("#book-select").addEventListener("change", async (event) => {
  clearTimeout(state.jobTimer);
  state.activeJobId = null;
  state.bookId = Number(event.target.value);
  state.controlPlane = null;
  state.controlPlaneBookId = null;
  state.promptDetail = null;
  resetMapStateForBook();
  await loadOverview();
});
$("#upload-button").addEventListener("click", () => $("#file-input").click());
$("#file-input").addEventListener("change", (event) => importFile(event.target.files[0]));
$("#library-button").addEventListener("click", openLibraryManager);
$("#library-close").addEventListener("click", () => $("#library-dialog").close());
$("#create-folder-button").addEventListener("click", createFolder);
$("#update-book-button").addEventListener("click", openUpdateDialog);
$("#update-close").addEventListener("click", () => $("#update-dialog").close());
$("#preview-update-button").addEventListener("click", submitBookUpdate);
$("#analyze-button").addEventListener("click", () => analyze(0));
$("#analysis-close").addEventListener("click", () => $("#analysis-dialog").close());
$("#analysis-reestimate").addEventListener("click", refreshAnalysisEstimate);
$("#analysis-start").addEventListener("click", startAnalysis);
$("#analysis-budget").addEventListener("input", renderAnalysisEstimate);
$("#analysis-review-mode").addEventListener("change", refreshAnalysisEstimate);
$("#key-settings-button").addEventListener("click", () => $("#key-dialog").showModal());
$("#key-close").addEventListener("click", () => $("#key-dialog").close());
$("#key-save").addEventListener("click", saveProviderKey);
$("#key-delete").addEventListener("click", deleteProviderKey);
$("#global-search").addEventListener("keydown", (event) => { if (event.key === "Enter") searchBook(); });
$("#global-search-button").addEventListener("click", searchBook);
$("#export-button").addEventListener("click", () => download(`/api/books/${state.bookId}/export?include_text=true`));
$("#backup-button").addEventListener("click", () => download("/api/backup"));
$("#delete-button").addEventListener("click", deleteCurrentBook);
$("#progress-slider").addEventListener("change", (event) => loadOverview(Number(event.target.value)));
$$(".nav-item").forEach((item) => item.addEventListener("click", () => { state.view = item.dataset.view; renderView(); }));
$("#inspector-close").addEventListener("click", closeInspector);
$("#scrim").addEventListener("click", closeInspector);
$("#source-close").addEventListener("click", () => $("#source-dialog").close());
$("#search-close").addEventListener("click", () => $("#search-dialog").close());
document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeInspector(); });
window.addEventListener("resize", () => {
  if (!$("#inspector").classList.contains("open")) return;
  $("#scrim").hidden = !window.matchMedia("(max-width: 1100px)").matches;
});

initialize();
