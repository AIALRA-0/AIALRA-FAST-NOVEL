// 单页应用只使用浏览器原生能力，减少首版构建与供应链成本；
const state = {
  books: [],
  folders: [],
  providers: [],
  bookId: null,
  overview: null,
  overviewRequestSerial: 0,
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
  map3DVolumeMode: ["shell", "section", "layer"].includes(window.localStorage.getItem("novel-atlas-3d-volume-mode"))
    ? window.localStorage.getItem("novel-atlas-3d-volume-mode") : "shell",
  mapPresentation: window.localStorage.getItem("novel-atlas-map-presentation") === "evidence" ? "evidence" : "atlas",
  mapLayout: null,
  narrativeMemory: null,
  knowledgeFacets: null,
  concepts: [],
  conceptsBookId: null,
  systems: [],
  mapStep: 0,
  mapTimer: null,
  mapPlaybackState: "idle",
  mapPlaybackRunId: 0,
  mapPlaybackSpeed: [0.5, 1, 1.5, 2].includes(Number(window.localStorage.getItem("novel-atlas-playback-speed")))
    ? Number(window.localStorage.getItem("novel-atlas-playback-speed")) : 1,
  mapLastConfirmedRegionId: null,
  mapAnimationFrame: null,
  mapMarkerPoint: null,
  mapMarkerPoint3D: null,
  map3DTargetPoint: null,
  mapPoints: null,
  mapViewport: null,
  mapViewportPersistTimer: null,
  mapTransitionRunId: 0,
  mapTransition: null,
  mapCameraRecords: null,
  mapCameraMode: ["world", "region", "step", "follow"].includes(window.localStorage.getItem("novel-atlas-map-camera-mode"))
    ? window.localStorage.getItem("novel-atlas-map-camera-mode") : "region",
  mapShowFullRoute: false,
  mapGraph: null,
  mapGraphResizeObserver: null,
  mapLabelFrame: null,
  mapRegionFrame: null,
  map3DRegionGroup: null,
  map3DActor: null,
  map3DNodes: null,
  map3DLinks: null,
  map3DCenter: null,
  mapRailTab: "chronology",
  mapChronologyCenter: 0,
  storyContextSerial: 0,
  inspectorTarget: null,
  inspectorRequestSerial: 0,
  analysisEstimate: null,
  analysisStartSegment: 0,
  qualityTab: "pending",
  budgetJobId: null,
  reviewTasks: [],
  narrativeStructure: null,
  storyScope: null,
  storyScopeBookId: null,
  controlPlane: null,
  controlPlaneBookId: null,
  promptDetail: null,
  viewBeforeLibrary: "relationships",
  libraryFolderId: "all",
  libraryBookId: null,
  libraryQuery: "",
  libraryEditingFolderId: null,
  libraryEditingBookId: null,
  dialogReturnFocus: new Map(),
  confirmResolver: null,
  formResolver: null,
  activeSelectBox: null,
  selectEnhanceFrame: null,
};

const labels = {
  relationships: ["人物关系", "谁与谁有关，以及关系从何而来"],
  timeline: ["剧情编年", "按故事时间排列，同时保留原文叙事位置"],
  map: ["逻辑地图", "地点、交通方式与主线人物的行动路径"],
  world: ["世界信息", "规则、力量、势力、背景与地理结构"],
  database: ["条目数据库", "可批量检索的物品、技能、属性、参数与术语"],
  systems: ["体系图谱", "只展示有原文依据的等级、组织、阶层与分类关系"],
  quality: ["质量检查", "查看分析进度、证据覆盖和需要人工确认的问题"],
  collaboration: ["分析设置与记录", "教系统理解小说；查看结果依据和本次分析记录"],
  library: ["书库管理", "用文件夹整理书籍，并查看分析与增量更新状态"],
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

const reportLanguageLabels = {
  follow_source: "跟随原文",
  "zh-CN": "中文",
  en: "English",
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

const MAP_CAMERA_STORAGE_KEY = "novel-atlas-map-camera-state-v2";
const MAP_CAMERA_SAFE_MARGIN = 0.28;
const MAP_CAMERA_MAX_RECORDS = 48;

function mapCameraScopeKey() {
  const scope = state.storyScope;
  return `${scope?.kind || "book"}:${scope?.id || state.bookId || "unknown"}`;
}

function mapCameraRecordKey(mapMode = state.mapMode, presentation = state.mapPresentation, scopeKey = mapCameraScopeKey(), bookId = state.bookId) {
  return `${Number(bookId) || "unknown"}|${scopeKey}|${presentation || "atlas"}|${mapMode || "2d"}`;
}

function loadMapCameraRecords() {
  if (state.mapCameraRecords) return state.mapCameraRecords;
  let parsed = null;
  try { parsed = JSON.parse(window.localStorage.getItem(MAP_CAMERA_STORAGE_KEY) || "null"); } catch { parsed = null; }
  state.mapCameraRecords = parsed && parsed.version === 2 && parsed.entries && typeof parsed.entries === "object"
    ? parsed : { version: 2, entries: {} };
  return state.mapCameraRecords;
}

function finiteNumber(value) {
  return Number.isFinite(Number(value)) ? Number(value) : null;
}

function mapCameraLabel(mode = state.mapCameraMode) {
  return mode === "world" ? "全世界" : mode === "step" ? "当前步骤" : mode === "follow" ? "跟随任务" : "当前区域";
}

function mapCameraSnapshot(mode = state.mapMode, presentation = state.mapPresentation, scopeKey = mapCameraScopeKey(), bookId = state.bookId) {
  const key = mapCameraRecordKey(mode, presentation, scopeKey, bookId);
  return loadMapCameraRecords().entries[key] || null;
}

function saveMapCameraSnapshot(snapshot, mode = state.mapMode, presentation = state.mapPresentation, scopeKey = mapCameraScopeKey(), bookId = state.bookId) {
  if (!snapshot || !bookId) return;
  const records = loadMapCameraRecords();
  const key = mapCameraRecordKey(mode, presentation, scopeKey, bookId);
  records.entries[key] = { ...snapshot, version: 2, updatedAt: Date.now() };
  const entries = Object.entries(records.entries)
    .sort((left, right) => Number(left[1]?.updatedAt || 0) - Number(right[1]?.updatedAt || 0))
    .slice(-MAP_CAMERA_MAX_RECORDS);
  records.entries = Object.fromEntries(entries);
  try { window.localStorage.setItem(MAP_CAMERA_STORAGE_KEY, JSON.stringify(records)); } catch { /* 存储空间不足时保留内存状态； */ }
}

function persistMapCameraState(mode = state.mapMode, presentation = state.mapPresentation, scopeKey = mapCameraScopeKey(), bookId = state.bookId) {
  const snapshot = state.mapViewportController?.capture?.();
  if (snapshot) saveMapCameraSnapshot(snapshot, mode, presentation, scopeKey, bookId);
  clearTimeout(state.mapViewportPersistTimer);
  state.mapViewportPersistTimer = null;
}

function scheduleMapCameraPersist() {
  clearTimeout(state.mapViewportPersistTimer);
  state.mapViewportPersistTimer = setTimeout(() => persistMapCameraState(), 140);
}

function cancelMapTransition() {
  cancelAnimationFrame(state.mapAnimationFrame);
  state.mapAnimationFrame = null;
  state.mapTransitionRunId += 1;
  state.mapTransition = null;
}

window.addEventListener("beforeunload", () => persistMapCameraState());
window.addEventListener("pagehide", () => persistMapCameraState());

function closeSelectBox({ restoreFocus = false } = {}) {
  const active = state.activeSelectBox;
  if (!active) return;
  active.popover.remove();
  active.button.setAttribute("aria-expanded", "false");
  state.activeSelectBox = null;
  if (restoreFocus) active.button.focus();
}

function syncSelectBox(select) {
  const wrapper = select.closest(".select-box");
  const button = wrapper?.querySelector(".select-box-button");
  if (!button) return;
  const selected = select.selectedOptions[0];
  button.querySelector(".select-box-value").textContent = selected?.textContent?.trim() || "请选择";
  button.disabled = select.disabled;
  button.setAttribute("aria-disabled", String(select.disabled));
}

function openSelectBox(select, button) {
  closeSelectBox();
  const options = [...select.options].filter((option) => !option.hidden);
  const popover = document.createElement("div");
  popover.className = "select-popover";
  popover.setAttribute("role", "listbox");
  popover.setAttribute("aria-label", select.getAttribute("aria-label") || select.labels?.[0]?.textContent || "选择选项");
  const searchable = options.length > 8;
  popover.innerHTML = `${searchable ? '<div class="select-search-wrap"><input class="select-search" type="search" placeholder="搜索选项" aria-label="搜索选项"></div>' : ""}<div class="select-options"></div>`;
  document.body.appendChild(popover);
  const optionHost = popover.querySelector(".select-options");
  const render = (query = "") => {
    const normalized = query.trim().toLocaleLowerCase();
    const visible = options.filter((option) => !normalized || option.textContent.toLocaleLowerCase().includes(normalized));
    optionHost.innerHTML = visible.length ? visible.map((option) => `<button class="select-option${option.selected ? " selected" : ""}" type="button" role="option" aria-selected="${option.selected}" data-value="${escapeHtml(option.value)}" ${option.disabled ? "disabled" : ""}><span>${escapeHtml(option.textContent.trim())}</span>${option.selected ? '<b aria-hidden="true">✓</b>' : ""}</button>`).join("") : '<div class="select-no-results">没有匹配选项</div>';
    optionHost.querySelectorAll(".select-option").forEach((item) => item.addEventListener("click", () => {
      select.value = item.dataset.value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      syncSelectBox(select);
      closeSelectBox({ restoreFocus: true });
    }));
  };
  render();
  const place = () => {
    const rect = button.getBoundingClientRect();
    const width = Math.min(Math.max(rect.width, 220), Math.max(220, window.innerWidth - 24));
    popover.style.width = `${width}px`;
    const measuredHeight = Math.min(popover.scrollHeight || 320, Math.max(180, window.innerHeight - 24));
    const below = window.innerHeight - rect.bottom;
    const openUp = below < Math.min(320, measuredHeight) && rect.top > below;
    popover.classList.toggle("opens-up", openUp);
    popover.style.left = `${Math.max(12, Math.min(window.innerWidth - width - 12, rect.left))}px`;
    popover.style.maxHeight = `${Math.max(160, openUp ? rect.top - 16 : window.innerHeight - rect.bottom - 16)}px`;
    if (openUp) {
      popover.style.top = "auto";
      popover.style.bottom = `${Math.max(12, window.innerHeight - rect.top + 6)}px`;
    } else {
      popover.style.bottom = "auto";
      popover.style.top = `${Math.min(window.innerHeight - 12, rect.bottom + 6)}px`;
    }
  };
  place();
  state.activeSelectBox = { select, button, popover, place };
  button.setAttribute("aria-expanded", "true");
  const search = popover.querySelector(".select-search");
  if (search) {
    search.addEventListener("input", () => render(search.value));
    search.focus();
  } else {
    optionHost.querySelector(".select-option.selected:not(:disabled), .select-option:not(:disabled)")?.focus();
  }
}

function enhanceSelect(select) {
  if (select.id === "book-select") return;
  if (!(select instanceof HTMLSelectElement) || select.dataset.selectBoxReady === "true") {
    if (select instanceof HTMLSelectElement) syncSelectBox(select);
    return;
  }
  select.dataset.selectBoxReady = "true";
  const wrapper = document.createElement("div");
  wrapper.className = `select-box${select.classList.contains("search-input") ? " select-box-search" : ""}`;
  select.parentNode.insertBefore(wrapper, select);
  wrapper.appendChild(select);
  select.classList.add("select-native");
  const button = document.createElement("button");
  button.type = "button";
  button.className = "select-box-button";
  button.setAttribute("aria-haspopup", "listbox");
  button.setAttribute("aria-expanded", "false");
  button.innerHTML = '<span class="select-box-value"></span><span class="select-box-chevron" aria-hidden="true"><svg viewBox="0 0 16 16" focusable="false"><path d="M4 6.25 8 10l4-3.75"/></svg></span>';
  wrapper.appendChild(button);
  button.addEventListener("click", () => state.activeSelectBox?.select === select ? closeSelectBox() : openSelectBox(select, button));
  button.addEventListener("keydown", (event) => {
    if (["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) {
      event.preventDefault();
      openSelectBox(select, button);
    }
    if (event.key === "Escape") closeSelectBox({ restoreFocus: true });
  });
  select.addEventListener("change", () => syncSelectBox(select));
  new MutationObserver(() => syncSelectBox(select)).observe(select, { childList: true, subtree: true, attributes: true });
  syncSelectBox(select);
}

function enhanceSelects(root = document) {
  root.querySelectorAll?.("select").forEach(enhanceSelect);
}

document.addEventListener("pointerdown", (event) => {
  if (!state.activeSelectBox) return;
  if (state.activeSelectBox.popover.contains(event.target) || state.activeSelectBox.button.contains(event.target)) return;
  closeSelectBox();
}, true);
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || !state.activeSelectBox) return;
  event.preventDefault();
  event.stopPropagation();
  closeSelectBox({ restoreFocus: true });
}, true);
window.addEventListener("resize", () => state.activeSelectBox?.place());
window.addEventListener("scroll", () => state.activeSelectBox?.place(), true);

// 所有后端文本都先转义，避免导入小说内容成为页面脚本；
function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function modelDisplayName(value) {
  return value === "evidence-demo-v1" ? "本地演示解析器" : value || "未记录";
}

function openDialog(selector, preferredSelector = ".icon-button") {
  const dialog = typeof selector === "string" ? $(selector) : selector;
  if (!dialog) return;
  state.dialogReturnFocus.set(dialog.id, document.activeElement);
  dialog.showModal();
  requestAnimationFrame(() => dialog.querySelector(preferredSelector)?.focus());
}

function closeDialog(selector) {
  const dialog = typeof selector === "string" ? $(selector) : selector;
  if (dialog?.open) dialog.close();
}

function confirmAction(title, message, confirmLabel = "确认") {
  const dialog = $("#confirm-dialog");
  if (!dialog) return Promise.resolve(false);
  $("#confirm-title").textContent = title;
  $("#confirm-message").textContent = message;
  $("#confirm-accept").textContent = confirmLabel;
  if (state.confirmResolver) state.confirmResolver(false);
  openDialog(dialog, "#confirm-cancel");
  return new Promise((resolve) => { state.confirmResolver = resolve; });
}

function finishConfirmation(accepted) {
  const resolve = state.confirmResolver;
  state.confirmResolver = null;
  closeDialog("#confirm-dialog");
  resolve?.(accepted);
}

function formAction({ title, description = "", submitLabel = "保存", fields = [] }) {
  const dialog = $("#form-dialog");
  if (!dialog) return Promise.resolve(null);
  if (state.formResolver) state.formResolver(null);
  $("#form-dialog-title").textContent = title;
  $("#form-dialog-description").textContent = description;
  $("#form-dialog-submit").textContent = submitLabel;
  $("#form-dialog-fields").innerHTML = fields.map((field) => {
    const attributes = `${field.required ? " required" : ""}${field.placeholder ? ` placeholder="${escapeHtml(field.placeholder)}"` : ""}`;
    if (field.type === "select") {
      const options = (field.options || []).map((option) => `<option value="${escapeHtml(option.value)}"${String(option.value) === String(field.value ?? "") ? " selected" : ""}>${escapeHtml(option.label)}</option>`).join("");
      return `<label>${escapeHtml(field.label)}<select name="${escapeHtml(field.name)}"${field.required ? " required" : ""}>${options}</select>${field.hint ? `<small>${escapeHtml(field.hint)}</small>` : ""}</label>`;
    }
    if (field.type === "textarea") {
      return `<label>${escapeHtml(field.label)}<textarea name="${escapeHtml(field.name)}" rows="${Number(field.rows || 4)}"${attributes}>${escapeHtml(field.value || "")}</textarea>${field.hint ? `<small>${escapeHtml(field.hint)}</small>` : ""}</label>`;
    }
    return `<label>${escapeHtml(field.label)}<input name="${escapeHtml(field.name)}" type="${escapeHtml(field.type || "text")}" value="${escapeHtml(field.value || "")}"${attributes}>${field.hint ? `<small>${escapeHtml(field.hint)}</small>` : ""}</label>`;
  }).join("");
  openDialog(dialog, fields[0] ? `[name="${fields[0].name}"]` : "#form-dialog-submit");
  return new Promise((resolve) => { state.formResolver = resolve; });
}

function finishFormAction(accepted) {
  const resolve = state.formResolver;
  if (!resolve) return;
  if (!accepted) {
    state.formResolver = null;
    closeDialog("#form-dialog");
    resolve(null);
    return;
  }
  const form = $("#form-dialog-fields");
  const invalid = form.querySelector(":invalid");
  if (invalid) {
    invalid.focus();
    invalid.reportValidity();
    return;
  }
  const values = Object.fromEntries(new FormData(form).entries());
  state.formResolver = null;
  closeDialog("#form-dialog");
  resolve(values);
}

// 数据库存储片段序号，页面统一换成可读的真实章节标题；
function chapterForSegment(ordinal) {
  const segment = state.overview?.segments?.find((item) => Number(item.ordinal) === Number(ordinal));
  return segment?.chapter_title || `第 ${Number(ordinal) + 1} 部分`;
}

// 模型账单以美元展示，并明确区分有价格快照和只有令牌统计的任务；
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
    cancelMapTransition();
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

function disposeMapGraph({ persist = true } = {}) {
  if (persist) persistMapCameraState();
  cancelMapTransition();
  cancelAnimationFrame(state.mapLabelFrame);
  state.mapLabelFrame = null;
  cancelAnimationFrame(state.mapRegionFrame);
  state.mapRegionFrame = null;
  state.mapGraphResizeObserver?.disconnect();
  state.mapGraphResizeObserver = null;
  disposeMap3DRegionMeshes();
  if (state.mapGraph) {
    state.mapGraph.pauseAnimation?.();
    state.mapGraph._destructor?.();
  }
  state.mapGraph = null;
  state.map3DActor = null;
  state.map3DNodes = null;
  state.map3DLinks = null;
  state.map3DCenter = null;
  state.map3DTargetPoint = null;
  state.mapMarkerPoint = null;
  state.mapMarkerPoint3D = null;
  state.mapViewportController = null;
}

function disposeMap3DRegionMeshes() {
  if (!state.map3DRegionGroup) return;
  state.map3DRegionGroup.traverse?.((object) => {
    object.geometry?.dispose?.();
    if (Array.isArray(object.material)) object.material.forEach((material) => material.dispose?.());
    else object.material?.dispose?.();
  });
  state.map3DRegionGroup.parent?.remove?.(state.map3DRegionGroup);
  state.map3DRegionGroup = null;
}

function installMap3DRegionMeshes(graph, centerX, centerY, attempt = 0) {
  if (state.mapGraph !== graph || state.mapPresentation !== "atlas") return;
  const regions = map3DVisibleRegions();
  const scene = graph.scene?.();
  if (!scene || !regions.length) return;
  let sampleMesh = null;
  scene.traverse?.((object) => {
    if (!sampleMesh && object.isMesh && object.geometry?.getAttribute?.("position")) sampleMesh = object;
  });
  if (!sampleMesh) {
    if (attempt < 30) requestAnimationFrame(() => installMap3DRegionMeshes(graph, centerX, centerY, attempt + 1));
    return;
  }
  const GroupConstructor = scene.constructor;
  const MeshConstructor = sampleMesh.constructor;
  const BufferGeometryConstructor = Object.getPrototypeOf(Object.getPrototypeOf(sampleMesh.geometry))?.constructor;
  const PositionAttributeConstructor = sampleMesh.geometry.getAttribute("position").constructor;
  const MaterialConstructor = sampleMesh.material.constructor;
  if (!GroupConstructor || !MeshConstructor || !BufferGeometryConstructor || !PositionAttributeConstructor || !MaterialConstructor) return;
  const group = new GroupConstructor();
  group.name = "novel-atlas-semantic-regions";
  const palette = [0x7cb5df, 0xb693d0, 0xe2b975, 0x7fc7b7, 0xdc94aa, 0x9ca5dc];
  const currentLocationId = Number(storyMapSteps()[state.mapStep]?.location_entity_id || 0);
  const emphasis = mapRegionEmphasis(currentLocationId || null, currentLocationId > 0);
  const currentNode = (state.mapLayout?.nodes || []).find((node) => Number(node.id) === currentLocationId);
  const currentZ = Number(currentNode?.z || 0) * (86 / 90);
  let created = 0;
  regions.forEach((region, regionIndex) => {
    const hull = (region.hull || []).filter((point) => Number.isFinite(Number(point.x)) && Number.isFinite(Number(point.y)));
    if (hull.length < 3) return;
    const sourceVolume = region.volume || {};
    const defaultThickness = String(region.kind).startsWith("evidence_") ? 28 : 18;
    let zMin = Number.isFinite(Number(sourceVolume.z_min)) ? Number(sourceVolume.z_min) * (86 / 90) : Number(region.containment_depth || 0) * 86 - defaultThickness;
    let zMax = Number.isFinite(Number(sourceVolume.z_max)) ? Number(sourceVolume.z_max) * (86 / 90) : Number(region.containment_depth || 0) * 86 + defaultThickness;
    const status = emphasis.status(region.id);
    if (state.map3DVolumeMode === "section") zMax = Math.max(zMin + 6, Math.min(zMax, currentZ + 8));
    if (state.map3DVolumeMode === "layer" && ["current", "ancestor"].includes(status)) {
      zMin = Math.max(zMin, currentZ - 7);
      zMax = Math.min(zMax, currentZ + 7);
      if (zMax - zMin < 6) { zMin = currentZ - 3; zMax = currentZ + 3; }
    }
    const vertices = [];
    const addPoint = (point, z) => vertices.push((Number(point.x) - centerX) * 0.72, -(Number(point.y) - centerY) * 0.72, z);
    const triangles = Array.isArray(region.surface_triangles) && region.surface_triangles.length
      ? region.surface_triangles
      : Array.from({ length: Math.max(0, hull.length - 2) }, (_, index) => [hull[0], hull[index + 1], hull[index + 2]]);
    triangles.forEach((triangle) => {
      addPoint(triangle[0], zMin); addPoint(triangle[2], zMin); addPoint(triangle[1], zMin);
      addPoint(triangle[0], zMax); addPoint(triangle[1], zMax); addPoint(triangle[2], zMax);
    });
    for (let index = 0; index < hull.length; index += 1) {
      const next = (index + 1) % hull.length;
      addPoint(hull[index], zMin); addPoint(hull[next], zMin); addPoint(hull[next], zMax);
      addPoint(hull[index], zMin); addPoint(hull[next], zMax); addPoint(hull[index], zMax);
    }
    const geometry = new BufferGeometryConstructor();
    geometry.setAttribute("position", new PositionAttributeConstructor(new Float32Array(vertices), 3));
    geometry.computeVertexNormals?.();
    const active = status === "current";
    const material = new MaterialConstructor({
      color: palette[Number(region.palette_index ?? regionIndex) % palette.length],
      transparent: true,
      opacity: state.map3DVolumeMode === "layer" && !["current", "ancestor"].includes(status) ? 0.015
        : active ? (String(region.kind).startsWith("evidence_") ? 0.48 : 0.38)
        : status === "ancestor" ? 0.22
          : status === "last" ? 0.13
            : (String(region.kind).startsWith("evidence_") ? 0.08 : 0.04),
      depthWrite: false,
    });
    const mesh = new MeshConstructor(geometry, material);
    mesh.name = `semantic-region:${region.id}`;
    mesh.renderOrder = -2;
    mesh.userData = { ...(mesh.userData || {}), regionId: region.id, boundaryKind: "semantic", active, emphasis: status, zMin, zMax, volumeMode: state.map3DVolumeMode };
    group.add(mesh);
    created += 1;
  });
  scene.add(group);
  state.map3DRegionGroup = group;
  const host = $("#map-3d");
  if (host) {
    host.dataset.regionMeshCount = String(created);
    host.dataset.regionVolumeCount = String(created);
    host.dataset.volumeMode = state.map3DVolumeMode;
  }
  graph.refresh?.();
}

function map3DVisibleRegions() {
  const regions = state.mapLayout?.regions || [];
  // 视角只改变相机和强调状态，不能从全世界视图中删除其他区域
  return regions.filter((region) => (region.node_ids || []).length && (region.hull || []).length >= 3);
}

function mapRegionEmphasis(locationId, remember = false) {
  const regions = state.mapLayout?.regions || [];
  const numericLocationId = locationId === null || locationId === undefined ? null : Number(locationId);
  const containing = numericLocationId === null ? [] : regions.filter(
    (region) => (region.node_ids || []).some((nodeId) => Number(nodeId) === numericLocationId),
  );
  containing.sort((left, right) => {
    const leftEvidence = String(left.kind).startsWith("evidence_") ? 1 : 0;
    const rightEvidence = String(right.kind).startsWith("evidence_") ? 1 : 0;
    return rightEvidence - leftEvidence
      || Number(right.containment_depth || 0) - Number(left.containment_depth || 0)
      || Number(left.member_count || left.node_ids?.length || 0) - Number(right.member_count || right.node_ids?.length || 0)
      || String(left.id).localeCompare(String(right.id));
  });
  const primary = containing[0] || null;
  if (remember && primary) state.mapLastConfirmedRegionId = String(primary.id);
  const containingIds = new Set(containing.map((region) => String(region.id)));
  return {
    primaryId: primary ? String(primary.id) : null,
    containingIds,
    lastId: primary ? null : state.mapLastConfirmedRegionId,
    status(regionId) {
      const id = String(regionId);
      if (id === this.primaryId) return "current";
      if (this.containingIds.has(id)) return "ancestor";
      if (id === this.lastId) return "last";
      return "secondary";
    },
  };
}

function syncMap2DRegions(locationId) {
  const emphasis = mapRegionEmphasis(locationId, locationId !== null && locationId !== undefined);
  $$(".semantic-region").forEach((element) => {
    const status = emphasis.status(element.dataset.region);
    element.classList.toggle("is-active", status === "current");
    element.classList.toggle("is-ancestor", status === "ancestor");
    element.classList.toggle("is-last-known", status === "last");
    element.classList.toggle("is-secondary", status === "secondary");
    element.dataset.emphasis = status;
  });
  return emphasis;
}

function resetMapStateForBook() {
  stopMapPlayback();
  disposeMapGraph({ persist: false });
  state.storyContextSerial += 1;
  state.mapStep = 0;
  state.mapViewport = null;
  state.mapMarkerPoint = null;
  state.mapMarkerPoint3D = null;
  state.map3DTargetPoint = null;
  state.activeLocation = null;
  state.mapPlaybackState = "idle";
  state.mapLastConfirmedRegionId = null;
  state.narrativeStructure = null;
  state.storyScope = null;
  state.storyScopeBookId = null;
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
  renderSidebarLibraryTree();
}

function renderSidebarLibraryTree() {
  const host = $("#sidebar-library-tree");
  if (!host) return;
  const expanded = new Set(JSON.parse(window.localStorage.getItem("novel-atlas-sidebar-folders") || "[]").map(Number));
  const children = new Map();
  state.folders.forEach((folder) => {
    const key = folder.parent_id === null ? "root" : String(folder.parent_id);
    if (!children.has(key)) children.set(key, []);
    children.get(key).push(folder);
  });
  const booksByFolder = new Map();
  state.books.forEach((book) => {
    const key = book.folder_id === null ? "root" : String(book.folder_id);
    if (!booksByFolder.has(key)) booksByFolder.set(key, []);
    booksByFolder.get(key).push(book);
  });
  const booksMarkup = (folderId) => (booksByFolder.get(String(folderId)) || []).map((book) => `<button class="sidebar-book${Number(state.bookId) === Number(book.id) ? " active" : ""}" data-book="${book.id}" type="button" title="${escapeHtml(book.title)}"><span aria-hidden="true">▤</span><span>${escapeHtml(book.title)}</span></button>`).join("");
  const folderMarkup = (parent = "root", depth = 0, trail = new Set()) => (children.get(String(parent)) || []).map((folder) => {
    if (trail.has(Number(folder.id))) return "";
    const nextTrail = new Set(trail).add(Number(folder.id));
    const open = expanded.has(Number(folder.id));
    return `<div class="sidebar-folder" style="--tree-depth:${depth}"><button class="sidebar-folder-toggle" data-folder="${folder.id}" aria-expanded="${open}" type="button"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 4.5 10 8l-4 3.5"/></svg><span>${escapeHtml(folder.name)}</span><small>${Number(folder.book_count || 0)}</small></button><div class="sidebar-folder-children" ${open ? "" : "hidden"}>${booksMarkup(folder.id)}${folderMarkup(folder.id, depth + 1, nextTrail)}</div></div>`;
  }).join("");
  host.innerHTML = `<div class="sidebar-root-books">${booksMarkup("root")}</div>${folderMarkup()}` || '<div class="sidebar-tree-empty">书库中还没有书籍</div>';
  host.querySelectorAll(".sidebar-folder-toggle").forEach((button) => button.addEventListener("click", () => {
    const id = Number(button.dataset.folder);
    if (expanded.has(id)) expanded.delete(id); else expanded.add(id);
    window.localStorage.setItem("novel-atlas-sidebar-folders", JSON.stringify([...expanded]));
    renderSidebarLibraryTree();
  }));
  host.querySelectorAll(".sidebar-book").forEach((button) => button.addEventListener("click", async () => {
    const selected = Number(button.dataset.book);
    if (selected === Number(state.bookId)) return;
    persistMapCameraState();
    state.bookId = selected;
    resetMapStateForBook();
    $("#book-select").value = String(selected);
    await loadOverview();
    renderSidebarLibraryTree();
  }));
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
  const requestSerial = ++state.overviewRequestSerial;
  if (Number(state.conceptsBookId) !== Number(requestedBookId)) {
    state.concepts = [];
    state.knowledgeFacets = null;
    state.conceptsBookId = null;
    state.inspectorRequestSerial += 1;
    if (state.inspectorTarget?.type === "concept") closeInspector();
  }
  if (!silent) $("#view-panel").innerHTML = '<div class="loading">正在整理证据与视图…</div>';
  const query = throughSegment === null ? "" : `?through_segment=${throughSegment}`;
  const [overview, mapLayout, narrativeMemory, knowledgeFacets, concepts, systems] = await Promise.all([
    api(`/api/books/${requestedBookId}/overview${query}`),
    api(`/api/books/${requestedBookId}/map-layout${query}`),
    api(`/api/books/${requestedBookId}/narrative-memory${query}`),
    api(`/api/books/${requestedBookId}/knowledge-facets`),
    api(`/api/books/${requestedBookId}/concepts?status=&limit=1000`),
    api(`/api/books/${requestedBookId}/systems`),
  ]);
  if (requestSerial !== state.overviewRequestSerial || state.bookId !== requestedBookId) return;
  const reviewTasks = await api(`/api/books/${requestedBookId}/review-tasks`);
  const narrativeStructure = await api(`/api/books/${requestedBookId}/narrative-structure`);
  if (requestSerial !== state.overviewRequestSerial || state.bookId !== requestedBookId) return;
  state.overview = overview;
  state.mapLayout = mapLayout;
  if (mapLayout.validation_state === "invalid") {
    state.mapPresentation = "evidence";
  }
  state.narrativeMemory = narrativeMemory;
  state.knowledgeFacets = knowledgeFacets;
  state.concepts = concepts;
  state.conceptsBookId = requestedBookId;
  state.systems = systems;
  state.reviewTasks = reviewTasks;
  state.narrativeStructure = narrativeStructure;
  if (Number(state.storyScopeBookId) !== Number(requestedBookId)) {
    const currentUnit = (narrativeStructure.units || []).find((unit) =>
      Number(unit.start_segment) <= Number(overview.through_segment)
      && Number(unit.end_segment) >= Number(overview.through_segment));
    state.storyScope = (narrativeStructure.worlds || []).length > 1 && currentUnit
      ? { kind: "world", id: Number(currentUnit.world_id) }
      : { kind: "book", id: Number(requestedBookId) };
    state.storyScopeBookId = Number(requestedBookId);
  }
  $("#systems-nav").hidden = !systems.some((system) => system.status === "active" && system.nodes?.length);
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
  $("#progress-count").textContent = `第 ${overview.through_segment + 1} 章 · 共 ${overview.segments.length} 章`;
  $("#progress-count").setAttribute("aria-label", `编辑防剧透进度，当前第 ${overview.through_segment + 1} 部分`);
}

function beginProgressEdit() {
  const holder = $(".progress-copy");
  if (!holder || holder.querySelector(".progress-inline-input") || !state.overview) return;
  const count = $("#progress-count");
  const input = document.createElement("input");
  input.className = "progress-inline-input";
  input.type = "number";
  input.min = "1";
  input.max = String(state.overview.segments.length);
  input.value = String(Number($("#progress-slider").value) + 1);
  input.setAttribute("aria-label", "输入已经读到的部分序号");
  count.hidden = true;
  count.after(input);
  let finished = false;
  const finish = async (commit) => {
    if (finished) return;
    finished = true;
    const next = Math.max(1, Math.min(state.overview.segments.length, Number.parseInt(input.value, 10) || 1));
    input.remove();
    count.hidden = false;
    if (!commit) return;
    $("#progress-slider").value = String(next - 1);
    count.textContent = `第 ${next} 章 · 共 ${state.overview.segments.length} 章`;
    await loadOverview(next - 1);
  };
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); finish(true); }
    if (event.key === "Escape") { event.preventDefault(); finish(false); }
  });
  input.addEventListener("blur", () => finish(true));
  input.focus();
  input.select();
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
  const reviewCard = $("#metric-review-card");
  reviewCard.hidden = unresolved === 0;
  reviewCard.classList.toggle("warning", unresolved > 0);
  reviewCard.innerHTML = unresolved > 0 ? `<span>需要核对身份</span><strong>${unresolved}</strong><small>原文证据暂时无法确认</small>` : "";
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
  panel.dataset.status = job.status;
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
      ? "正文片段已经全部复用，本次只整理跨章节人物、时间和世界信息"
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
  $("#analysis-estimate").innerHTML = `<strong>任务已用 ${escapeHtml(formatCost(job))}</strong><br>当前自动范围 $${Number(job.max_cost_usd || 0).toFixed(2)} · 输入 ${Number(job.input_tokens || 0).toLocaleString()}/${Number(job.max_input_tokens || 0).toLocaleString()} · 输出 ${Number(job.output_tokens || 0).toLocaleString()}/${Number(job.max_output_tokens || 0).toLocaleString()}<br>保存后任务保持暂停，点击“继续”会恢复自动适配`;
  $("#analysis-reestimate").hidden = true;
  document.querySelector('label[for="analysis-review-mode"]')?.setAttribute("hidden", "");
  $("#analysis-review-mode").hidden = true;
  $("#analysis-start").textContent = "保存新上限";
  $("#analysis-start").disabled = false;
  openDialog("#analysis-dialog", "#analysis-budget");
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
  document.body.dataset.view = state.view;
  disposeRelationshipGraph();
  disposeMapGraph();
  if (state.view !== "map") stopMapPlayback();
  if (state.view === "map") closeInspector();
  document.body.dataset.view = state.view;
  renderHeader();
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === state.view));
  const renderers = {
    relationships: renderRelationships,
    timeline: renderTimeline,
    map: renderMap,
    world: renderWorld,
    database: renderDatabase,
    systems: renderSystems,
    quality: renderQuality,
    collaboration: renderCollaboration,
    library: renderLibraryManager,
  };
  renderers[state.view]();
  bindStoryScope();
}

function panelHead(title, description, legend = "") {
  const scopedViews = new Set(["relationships", "timeline", "map", "world", "database"]);
  return `<header class="panel-head"><div><h2>${escapeHtml(title)}</h2><p>${escapeHtml(description)}</p></div>${legend}</header>${scopedViews.has(state.view) ? storyScopeControl() : ""}`;
}

function storyScopeControl() {
  const structure = state.narrativeStructure;
  if (!structure || (structure.scope_options || []).length <= 1) return "";
  const selected = `${state.storyScope?.kind || "book"}:${state.storyScope?.id || state.bookId}`;
  const book = (structure.scope_options || []).filter((item) => item.kind === "book");
  const worlds = (structure.scope_options || []).filter((item) => item.kind === "world");
  const units = (structure.scope_options || []).filter((item) => item.kind === "unit");
  const options = (items) => items.map((item) => `<option value="${item.kind}:${item.id}" ${`${item.kind}:${item.id}` === selected ? "selected" : ""}>${escapeHtml(item.label)}</option>`).join("");
  return `<div class="story-scope-bar"><label for="story-scope-select">查看范围</label><select id="story-scope-select" aria-label="选择整本书、故事世界或独立故事"><optgroup label="整本书">${options(book)}</optgroup>${worlds.length ? `<optgroup label="故事世界">${options(worlds)}</optgroup>` : ""}${units.length ? `<optgroup label="故事单元">${options(units)}</optgroup>` : ""}</select><span>分区只是可撤销的阅读整理；不会拆成多本书</span></div>`;
}

function bindStoryScope() {
  const select = $("#story-scope-select");
  if (select) select.dataset.scopeReady = "true";
}

function activeScopeUnits() {
  const units = state.narrativeStructure?.units || [];
  if (!state.storyScope || state.storyScope.kind === "book") return units;
  if (state.storyScope.kind === "unit") return units.filter((unit) => Number(unit.id) === Number(state.storyScope.id));
  return units.filter((unit) => Number(unit.world_id) === Number(state.storyScope.id));
}

function storyScopeEntityIds() {
  if (!state.storyScope || state.storyScope.kind === "book") return null;
  return new Set(activeScopeUnits().flatMap((unit) => unit.entity_ids || []).map(Number));
}

function inStoryScope(item) {
  if (!state.storyScope || state.storyScope.kind === "book") return true;
  if (item.kind && item.id !== undefined) return storyScopeEntityIds()?.has(Number(item.id)) || false;
  if (item.first_segment === null || item.first_segment === undefined || item.first_segment === "") return true;
  const ordinal = Number(item.first_segment);
  return activeScopeUnits().some((unit) => Number(unit.start_segment) <= ordinal && Number(unit.end_segment) >= ordinal);
}

function emptyState(title, message) {
  return `<div class="empty-state"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(message)}</p></div>`;
}

function relationDisplay(claim) {
  if (claim.directionality !== "bidirectional") {
    return `${claim.source_name} —${claim.predicate}→ ${claim.target_name}`;
  }
  const reverse = claim.reverse_predicate || claim.predicate;
  const predicate = reverse === claim.predicate ? claim.predicate : `${claim.predicate} ⇄ ${reverse}`;
  return `${claim.source_name} —${predicate}— ${claim.target_name}`;
}

function relationPredicateLabel(claim) {
  if (claim.directionality !== "bidirectional") return claim.predicate;
  const reverse = claim.reverse_predicate || claim.predicate;
  return reverse === claim.predicate ? claim.predicate : `${claim.predicate} ⇄ ${reverse}`;
}

const knowledgePredicateLabels = {
  summary: "核心说明",
  description: "详细说明",
  identity: "身份",
  affiliation: "归属",
  location: "所在地点",
  status: "当前状态",
};

const knowledgeSourceLabels = {
  original_text: "原文事实",
  human_note: "人工说明",
  external_fact: "外部资料",
  migrated_entry: "旧数据迁移",
  migrated_world_note: "旧世界信息迁移",
};

const knowledgeStatusLabels = {
  accepted: "已确认",
  parallel: "证据并列",
  needs_resolution: "等待解决",
  deprecated: "已弃用",
};

function knowledgePredicateLabel(value) {
  return knowledgePredicateLabels[value] || String(value || "未命名属性").replaceAll("_", " ");
}

function knowledgeSourceLabel(value) {
  return knowledgeSourceLabels[value] || "系统整理";
}

function knowledgeStatusLabel(value) {
  return knowledgeStatusLabels[value] || "状态未知";
}

// 正式关系图始终返回全部已确认节点和关系；缩放只调整标签密度，不能删除事实；
function relationshipDataset(allEntities, allClaims) {
  const degree = new Map(allEntities.map((node) => [node.id, 0]));
  allClaims.forEach((claim) => {
    degree.set(claim.source_entity_id, (degree.get(claim.source_entity_id) || 0) + 1);
    degree.set(claim.target_entity_id, (degree.get(claim.target_entity_id) || 0) + 1);
  });
  return { entities: allEntities, claims: allClaims, degree };
}

function renderRelationships() {
  const relationshipEntities = state.overview.entities.filter((item) => ["person", "faction"].includes(item.kind) && inStoryScope(item));
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
  const mergeReview = mergeCandidates.length ? `<details class="merge-review warning"><summary>需要确认 ${mergeCandidates.length} 组人物身份</summary><p>系统无法仅凭现有证据判断这些名字是否属于同一人物；可以在“质量检查”中逐条查看原因和处理影响</p><button class="button button-primary open-review-center" type="button">前往待处理事项</button></details>` : "";
  const isolatedSection = confirmedIsolated.length || unresolvedConnectivity.length ? `<section class="connectivity-review-section">
    ${confirmedIsolated.length ? `<details><summary>已确认孤立 ${confirmedIsolated.length} 个</summary><div class="connectivity-card-grid">${confirmedIsolated.map((item) => `<button class="connectivity-card target-button" data-type="entity" data-id="${item.entity_id}" type="button"><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.reason)}</span><small>已扫描 ${item.scanned_segment_count} 个原文片段 · ${item.mention_count} 次提及</small></button>`).join("")}</div></details>` : ""}
    ${unresolvedConnectivity.length ? `<details class="warning" open><summary>${unresolvedConnectivity.length} 位人物的关系需要确认</summary><p>系统会先自动扫描原文；仍无可靠证据时，可以确认其暂时独立</p><button class="button button-primary open-review-center" type="button">查看并处理</button></details>` : ""}
  </section>` : "";
  if (!allEntities.length) {
    $("#view-panel").innerHTML = panelHead("人物关系网", "点击节点查看身份、别名和原文证据", legend) + mergeReview + emptyState("还没有通过复审的关系", "孤立节点会先扫描全部提及窗口，确认关系或确认孤立后再进入相应区域") + isolatedSection;
    bindMergeReview();
    bindTargets();
    $$(".open-review-center").forEach((button) => button.addEventListener("click", () => { state.view = "quality"; state.qualityTab = "pending"; renderView(); }));
    return;
  }
  const fallbackEntities = entities.map((node) => `<li><button class="text-link target-button" data-type="entity" data-id="${node.id}">${escapeHtml(node.name)}</button> · ${escapeHtml(categoryLabels[node.kind] || node.kind)}</li>`).join("");
  const fallbackClaims = claims.map((claim) => `<li><button class="text-link target-button" data-type="claim" data-id="${claim.id}">${escapeHtml(relationDisplay(claim))}</button></li>`).join("");
  const interactionHint = state.relationshipMode === "3d" ? "拖动空白处旋转，拖动节点固定位置，滚轮缩放；悬停会突出当前关系" : "拖动空白处平移，拖动节点固定位置，滚轮缩放；悬停会突出当前关系";
  $("#view-panel").innerHTML = `${panelHead("人物关系网", interactionHint, legend)}${mergeReview}
    <div class="graph-toolbar" aria-label="关系图控制">
      <div class="graph-control-groups"><div class="segmented-control"><button class="graph-mode${state.relationshipMode === "3d" ? " active" : ""}" data-mode="3d" type="button">3D 探索</button><button class="graph-mode${state.relationshipMode === "2d" ? " active" : ""}" data-mode="2d" type="button">2D 平面</button></div></div>
      <div class="graph-actions"><button id="graph-fit" class="button button-quiet" type="button">适合窗口</button><button id="graph-reset" class="button button-quiet" type="button">重置视角</button><button id="graph-unpin" class="button button-quiet" type="button">重新自动布局</button></div>
      <span>全量展示 ${entities.length} 个节点 · ${claims.length} 条关系</span>
    </div>
    <div class="force-graph-shell"><div id="relationship-graph" class="force-graph" role="img" aria-label="可旋转和拖动的人物关系图"></div><div id="relationship-labels" class="relationship-labels" aria-hidden="true"></div><div id="relationship-focus" class="relationship-focus">把鼠标移到人物或关系线上，查看单独关系</div></div>
    <details class="fallback-list"><summary>查看全部人物与关系的文字列表</summary><h3>人物与势力</h3><ul>${fallbackEntities}</ul><h3>全部关系</h3><ul>${fallbackClaims}</ul></details>${isolatedSection}`;
  bindTargets();
  bindMergeReview();
  $$(".open-review-center").forEach((button) => button.addEventListener("click", () => {
    state.view = "quality";
    state.qualityTab = "pending";
    renderView();
  }));
  createRelationshipGraph(entities, claims);
}

function createRelationshipGraph(entities, claims) {
  if (state.relationshipMode === "2d") {
    createRelationshipGraph2D(entities, claims);
    return;
  }
  const host = $("#relationship-graph");
  if (!host || typeof window.ForceGraph3D !== "function") {
    host.innerHTML = emptyState("关系图组件没有载入", "仍可使用下方文字列表查看人物和证据");
    return;
  }
  const pairCounts = new Map();
  const pairIndexes = new Map();
  const baseLinks = claims.map((claim) => {
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
      visualDirection: "forward",
    };
  });
  baseLinks.forEach((link) => {
    const count = pairCounts.get(link.pairKey) || 1;
    link.curvature = count === 1 ? 0 : (link.pairIndex - (count - 1) / 2) * 0.18;
  });
  const links = baseLinks.flatMap((link) => link.directionality === "bidirectional"
    ? [link, {
      ...link,
      source: link.target_entity_id,
      target: link.source_entity_id,
      visualDirection: "reverse",
      curvature: link.curvature,
    }]
    : [link]);
  const degree = new Map(entities.map((node) => [node.id, 0]));
  baseLinks.forEach((link) => {
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
      focus.innerHTML = `<strong>${escapeHtml(relationDisplay(hoveredLink))}</strong><span>${escapeHtml(hoveredLink.summary)} · 点击关系线查看原文证据</span>`;
      return;
    }
    if (hoveredNode) {
      const related = claims.filter((claim) => claim.source_entity_id === hoveredNode.id || claim.target_entity_id === hoveredNode.id);
      focus.innerHTML = `<strong>${escapeHtml(hoveredNode.name)}</strong><span>${escapeHtml(hoveredNode.summary)} · ${related.length} 条可核验关系</span>`;
      return;
    }
    focus.textContent = "把鼠标移到人物或关系线上，查看单独关系";
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
      if (state.relationshipHover?.link && Number(state.relationshipHover.link.id) === Number(link.id)) return semanticPalette.current;
      const hoveredNode = state.relationshipHover?.node;
      if (hoveredNode) return linkedToHovered(link, hoveredNode.id) ? "#222222" : "#dededa";
      if (state.relationshipHover?.link) return "#dededa";
      return "#92928e";
    })
    .linkOpacity(0.48)
    .linkWidth((link) => (state.relationshipHover?.link && Number(state.relationshipHover.link.id) === Number(link.id)) || (state.relationshipHover?.node && linkedToHovered(link, state.relationshipHover.node.id)) ? 3.2 : link.visualDirection === "reverse" ? 0.7 : 1.2)
    .linkCurvature((link) => link.curvature)
    .linkDirectionalArrowLength(6.2)
    .linkDirectionalArrowRelPos(0.76)
    .linkDirectionalArrowColor((link) => state.relationshipHover?.link && Number(state.relationshipHover.link.id) === Number(link.id) ? semanticPalette.current : "#666663")
    .linkHoverPrecision(10)
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
      renderRelationshipLabels(graph, nodes, links, currentNodeIds(), refreshFocus);
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
      toast("固定位置已清除，关系图正在重新排布");
    } catch (error) {
      toast(error.message, true);
    }
  });
}

// 二维阅读模式使用 fCoSE 的碰撞、组件打包和增量约束，密集图不再依赖手写坐标修补；
function createRelationshipGraph2D(entities, claims) {
  const host = $("#relationship-graph");
  if (!host || typeof window.cytoscape !== "function") {
    host.innerHTML = emptyState("二维关系图组件没有载入", "仍可切换到三维模式或使用下方文字列表");
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
        reversePredicate: claim.reverse_predicate || claim.predicate,
        directionality: claim.directionality || "directed",
        displayPredicate: relationPredicateLabel(claim),
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
    minZoom: 0.03,
    maxZoom: 12,
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
          "source-arrow-color": "#718d83",
          "source-arrow-shape": "none",
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
      { selector: "edge[directionality = 'bidirectional']", style: { "source-arrow-shape": "triangle" } },
      { selector: ".muted", style: { opacity: 0.09, "text-opacity": 0 } },
      { selector: "node.focused", style: { "border-color": semanticPalette.current, "border-width": 6, "z-index": 20 } },
      { selector: "edge.focused", style: { opacity: 1, width: 3.2, "line-color": semanticPalette.person, "target-arrow-color": semanticPalette.current, "source-arrow-color": semanticPalette.current, label: "data(displayPredicate)", "z-index": 18 } },
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
    if (focus) focus.textContent = "把鼠标移到人物或关系线上，查看单独关系";
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
    if (focus) focus.innerHTML = `<strong>${escapeHtml(edge.data("sourceName"))} —${escapeHtml(edge.data("displayPredicate"))}— ${escapeHtml(edge.data("targetName"))}</strong><span>${escapeHtml(edge.data("summary"))} · 点击关系线查看原文证据</span>`;
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
      toast("固定位置已清除，二维关系图正在重新排布");
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

function renderRelationshipLabels(graph, nodes, links, visibleIds, refreshFocus = () => {}) {
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
    labels.push({ node, x: placement.x, y: placement.y });
  }
  const retained = new Set(labels.map((item) => String(item.node.id)));
  layer.querySelectorAll(".relationship-label").forEach((label) => {
    if (!retained.has(label.dataset.node)) label.remove();
  });
  labels.forEach(({ node, x, y }) => {
    let label = layer.querySelector(`.relationship-label[data-node="${node.id}"]`);
    if (!label) {
      label = document.createElement("button");
      label.className = "relationship-label";
      label.dataset.node = String(node.id);
      label.type = "button";
      label.textContent = node.name;
      label.addEventListener("pointerenter", () => {
        state.relationshipHover = { node, link: null };
        graph.refresh();
        refreshFocus();
      });
      label.addEventListener("pointerleave", () => {
        state.relationshipHover = { node: null, link: null };
        graph.refresh();
        refreshFocus();
      });
      label.addEventListener("click", () => openInspector("entity", Number(node.id)));
      layer.appendChild(label);
    }
    label.style.left = `${x}px`;
    label.style.top = `${y}px`;
    label.classList.toggle("active", Number(node.id) === Number(hoveredId));
  });
}

function bindMergeReview() {
  $$(".merge-choice").forEach((button) => button.addEventListener("click", async () => {
    try {
      await api(`/api/books/${state.bookId}/entities/merge`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keep_entity_id: Number(button.dataset.keep), remove_entity_id: Number(button.dataset.remove), reason: "用户确认同一实体" }),
      });
      toast("人物资料已合并，关系和证据已经转移");
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

// 编年、地图、详情和播放按钮都从同一有序步骤读取数据；
// 旧数据库无需重跑；overview.events 是兼容回退，不会生成第二套顺序；
function storyMapSteps() {
  const steps = state.overview?.story_map_steps || state.overview?.events || [];
  return steps.filter(inStoryScope);
}

function eventNarrativeText(event) {
  const memory = state.narrativeMemory?.recent_scenes?.find((item) => Number(item.id) === Number(event?.id));
  return memory?.narrative_text || event?.summary || "";
}

function renderTimeline() {
  const events = storyMapSteps();
  if (!events.length) {
    $("#view-panel").innerHTML = panelHead("剧情编年史", "从上往下按故事发生时间排列") + emptyState("还没有可核验事件", "事件必须带原文引文；时间无法确定时会明确标成未知");
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
  const conflictNotice = conflictCount ? `<div class="timeline-warning">${conflictCount} 条互相冲突的时间约束已经隔离，没有参与当前排序</div>` : "";
  const toolbar = `<div class="timeline-toolbar"><div class="segmented-control"><button class="timeline-mode${state.timelineMode === "story" ? " active" : ""}" data-mode="story" type="button">故事编年</button><button class="timeline-mode${state.timelineMode === "narrative" ? " active" : ""}" data-mode="narrative" type="button">原文顺序</button></div><span>两种顺序独立保存，回忆不会再冒充当前事件</span></div>`;
  const openThreads = (state.narrativeMemory?.open_threads || []).filter((item) => item.status === "open").slice(0, 8);
  const characterStates = (state.narrativeMemory?.character_states || []).filter((item) => item.goal || item.states?.length).slice(0, 8);
  const memoryPanel = openThreads.length || characterStates.length ? `<details class="narrative-memory"><summary>当前承接记忆 · ${openThreads.length} 条未闭合线索</summary><div class="narrative-memory-content">${openThreads.length ? `<article><strong>尚未解决</strong><p>${openThreads.map((item) => escapeHtml(item.title)).join(" · ")}</p></article>` : ""}${characterStates.length ? `<article><strong>人物当前状态</strong><p>${characterStates.map((item) => `${escapeHtml(item.name)}：${escapeHtml(item.goal || item.states?.join("、") || "状态已记录")}${item.location_name ? `，位于${escapeHtml(item.location_name)}` : ""}`).join("<br>")}</p></article>` : ""}</div></details>` : "";
  $("#view-panel").innerHTML = panelHead("剧情编年史", "故事编年由无环时间约束计算，原文顺序只表示作者何时讲到这件事") + toolbar + conflictNotice + memoryPanel + `<div class="timeline">${cards}</div>`;
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

// 后端快照不可用时使用确定性的黄金角投影；它只保证稳定和可读，不伪造方位；
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

// 逻辑地图使用与关系图相同的成熟约束布局；原文明示的方位会成为硬相对位置约束；
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
      // 百级地点图使用 Cytoscape 内置 CoSE；它对大量弱连接分量更稳定，再由下方投影恢复
      // 原文明示的东南西北约束，避免 fCoSE 在极端分量上生成无效内部网格；
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
      // 每轮只移动一小段，保留 CoSE 的整体拓扑，同时把可验证方位投影回正确象限；
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
    // 大部头地图保留节点之间的可读距离，画布可以超出视窗并通过拖动、缩放查看；
    // 把整本书的全部地点强行压进 900×470 会让文字重叠，局部行程也无法辨认；
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
    console.warn("地图约束布局失败，已使用保守布局", error);
    return stableTopologyFallback(locations);
  }
}

function mapLabelLines(name, maxWidth = 150, maxLines = 2) {
  let remaining = String(name || "").trim().replace(/\s+/g, " ");
  if (!remaining) return [""];
  const widthOf = (value) => [...value].reduce((total, character) => total + (character.charCodeAt(0) > 127 ? 12 : 7.2), 0);
  const lines = [];
  while (remaining && lines.length < maxLines) {
    let width = 0;
    let splitAt = 0;
    let lastBreak = 0;
    for (let index = 0; index < remaining.length; index += 1) {
      const character = remaining[index];
      width += character.charCodeAt(0) > 127 ? 12 : 7.2;
      if (" -—/·".includes(character)) lastBreak = index + 1;
      if (width > maxWidth) {
        splitAt = lastBreak || Math.max(1, index);
        break;
      }
    }
    if (!splitAt) {
      lines.push(remaining.trim());
      remaining = "";
    } else {
      lines.push(remaining.slice(0, splitAt).trim());
      remaining = remaining.slice(splitAt).trim();
    }
  }
  if (remaining) {
    let last = lines.at(-1).replace(/…$/, "");
    while (last && widthOf(`${last}…`) > maxWidth) last = last.slice(0, -1);
    lines[lines.length - 1] = `${last.trim()}…`;
  }
  return lines;
}

function mapDisplayName(name) {
  return mapLabelLines(name).join(" ");
}

function svgTextLines(lines, x, y, anchor = "middle", lineHeight = 15) {
  return lines.map((line, index) => `<tspan x="${x}" dy="${index ? lineHeight : 0}" text-anchor="${anchor}">${escapeHtml(line)}</tspan>`).join("");
}

// 地名从四个方向选择空位，并用底色保持线路穿过时仍然可读；
function mapLabelPlacements(locations, points, journey, currentLocationId) {
  const snapshotNodes = new Map((state.mapLayout?.nodes || []).map((node) => [Number(node.id), node]));
  if (locations.length && locations.every((location) => snapshotNodes.get(Number(location.id))?.label_placement?.bbox)) {
    return new Map(locations.map((location) => {
      const placement = snapshotNodes.get(Number(location.id)).label_placement;
      return [Number(location.id), {
        x: Number(placement.x), y: Number(placement.y), anchor: placement.anchor || "middle",
        width: Number(placement.width || 52), box: placement.bbox,
        visible: Boolean(placement.visible) || Number(location.id) === Number(currentLocationId),
      }];
    }));
  }
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
  const allLocations = state.overview.entities.filter((item) => item.kind === "place" && inStoryScope(item));
  const people = state.overview.entities.filter((item) => item.kind === "person" && inStoryScope(item));
  const journey = storyMapSteps();
  const allRoutes = (state.overview.routes || []).filter(inStoryScope);
  const routeByEventId = new Map(allRoutes.filter((route) => route.event_id !== null).map((route) => [Number(route.event_id), route]));
  const scopedEntityIds = storyScopeEntityIds();
  const allGeographyRelations = (state.overview.geography_relations || []).filter((relation) =>
    !scopedEntityIds || (scopedEntityIds.has(Number(relation.source_entity_id)) && scopedEntityIds.has(Number(relation.target_entity_id))));
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
  // 地图必须保留所有已识别地点；未进入主角行程的地点可以降低标签优先级，
  // 但不能从二维、三维或语义区域中消失；
  allLocations.forEach((location) => mappedLocationIds.add(Number(location.id)));
  const locations = allLocations.filter((location) => mappedLocationIds.has(Number(location.id)));
  const geographyRelations = allGeographyRelations.filter(
    (relation) => mappedLocationIds.has(Number(relation.source_entity_id)) && mappedLocationIds.has(Number(relation.target_entity_id)),
  );
  const rawRouteTopology = allRoutes.filter((route) =>
    route.from_id !== null && route.to_id !== null
      && mappedLocationIds.has(Number(route.from_id)) && mappedLocationIds.has(Number(route.to_id))
  );
  // 同一对地点可能在多个章节反复往返；地图底层只画一条拓扑边，逐步行程仍按编年完整保留；
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
    $("#view-panel").innerHTML = panelHead("逻辑地图与故事编年", "地图按照故事编年逐步显示地点、人物和事件", legend) + protagonistPicker + emptyState("还没有可核验地点", "编年步骤仍然完整保留，地点证据补齐后会自动进入地图");
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
  const snapshotBounds = state.storyScope?.kind === "book" ? state.mapLayout?.world_bounds : null;
  state.mapBounds = snapshotBounds ? {
    minX: Number(snapshotBounds.min_x), maxX: Number(snapshotBounds.max_x),
    minY: Number(snapshotBounds.min_y), maxY: Number(snapshotBounds.max_y),
  } : {
    minX: Math.min(...pointValues.map((point) => point.x)) - 110,
    maxX: Math.max(...pointValues.map((point) => point.x)) + 110,
    minY: Math.min(...pointValues.map((point) => point.y)) - 90,
    maxY: Math.max(...pointValues.map((point) => point.y)) + 90,
  };
  state.mapStep = Math.max(0, Math.min(state.mapStep, Math.max(0, journey.length - 1)));
  const paper = `<rect class="map-paper-plane" x="${state.mapBounds.minX}" y="${state.mapBounds.minY}" width="${state.mapBounds.maxX - state.mapBounds.minX}" height="${state.mapBounds.maxY - state.mapBounds.minY}"></rect>`;
  const visibleLocationIds = new Set(locations.map((location) => Number(location.id)));
  const initialRegionEmphasis = mapRegionEmphasis(currentLocationId, currentLocationId !== null && currentLocationId !== undefined);
  const semanticRegions = state.mapPresentation === "atlas" ? (state.mapLayout?.regions || []).map((region, index) => {
    const regionNodes = (region.node_ids || []).filter((nodeId) => visibleLocationIds.has(Number(nodeId)));
    if (!regionNodes.length || (region.hull || []).length < 3) return "";
    const emphasisStatus = initialRegionEmphasis.status(region.id);
    const path = region.hull.map((point, pointIndex) => `${pointIndex ? "L" : "M"} ${Number(point.x)} ${Number(point.y)}`).join(" ");
    const labelAnchor = region.label_anchor || region.centroid || {};
    const connector = region.label_connector || {};
    const evidenceRegion = String(region.kind).startsWith("evidence_");
    const regionClass = evidenceRegion ? "is-evidence" : "is-topology";
    const regionName = region.display_name || region.label;
    const labelBox = labelAnchor.bbox || {};
    const regionLines = Array.isArray(labelAnchor.lines) && labelAnchor.lines.length ? labelAnchor.lines : mapLabelLines(regionName, 166, 3);
    const labelWidth = Number(labelAnchor.width || (Number(labelBox.max_x) - Number(labelBox.min_x))) || Math.max(72, ...regionLines.map((line) => [...line].length * 13 + 22));
    const labelHeight = Number(labelAnchor.height || (Number(labelBox.max_y) - Number(labelBox.min_y))) || 25 + Math.max(0, regionLines.length - 1) * 15;
    const labelX = Number.isFinite(Number(labelBox.min_x)) ? Number(labelBox.min_x) : Number(labelAnchor.x) - labelWidth / 2;
    const labelY = Number.isFinite(Number(labelBox.min_y)) ? Number(labelBox.min_y) : Number(labelAnchor.y) - labelHeight + 5;
    const textY = labelY + 17;
    const label = Number.isFinite(Number(labelAnchor.x)) && Number.isFinite(Number(labelAnchor.y))
      ? `<g class="semantic-region-label-wrap" data-placement="${escapeHtml(labelAnchor.placement_mode || "side_lane")}">${connector.hidden ? "" : `<line class="semantic-region-label-connector" x1="${Number(connector.x1 ?? labelAnchor.x)}" y1="${Number(connector.y1 ?? labelAnchor.y)}" x2="${Number(connector.x2 ?? labelAnchor.x)}" y2="${Number(connector.y2 ?? labelAnchor.y)}"></line>`}<rect class="semantic-region-label-bg" x="${labelX}" y="${labelY}" width="${labelWidth}" height="${labelHeight}" rx="8"></rect><text class="semantic-region-label" x="${Number(labelAnchor.x)}" y="${textY}">${svgTextLines(regionLines, Number(labelAnchor.x), textY)}</text></g>`
      : "";
    const emphasisClass = emphasisStatus === "current" ? " is-active" : emphasisStatus === "ancestor" ? " is-ancestor" : emphasisStatus === "last" ? " is-last-known" : " is-secondary";
    const regionDescription = region.kind === "evidence_containment" ? "原文包含区域" : region.kind === "evidence_proximity" ? "原文相邻地点区域" : "故事组织区域";
    return `<g class="semantic-region ${regionClass}${emphasisClass} region-${Number(region.palette_index ?? index) % 6}" data-region="${escapeHtml(region.id)}" data-emphasis="${emphasisStatus}" tabindex="0" role="button"><path d="${path} Z"></path>${label}<title>${escapeHtml(regionName)}；${regionDescription}；轮廓不代表真实地理边界</title></g>`;
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
    const labelLines = Array.isArray(label.lines) && label.lines.length ? label.lines : mapLabelLines(location.name, 150, 2);
    const labelHeight = Number(label.height || (24 + Math.max(0, labelLines.length - 1) * 15));
    const rectX = label.anchor === "middle" ? label.x - label.width / 2 : label.anchor === "start" ? label.x - 5 : label.x - label.width + 5;
    const labelClass = label.visible ? "map-node-label" : "map-node-label map-node-label-collided";
    return `<g class="map-node graph-node${journeyLocationIds.has(Number(location.id)) ? " main-line" : ""}" data-location="${location.id}" tabindex="0" role="button" aria-label="跳到${escapeHtml(location.name)}发生的剧情"><circle cx="${point.x}" cy="${point.y}" r="20"></circle><g class="${labelClass}"><line class="map-label-stem" x1="${point.x}" y1="${point.y}" x2="${label.x}" y2="${label.y - 4}"></line><rect class="map-label-bg" x="${rectX}" y="${label.y - 17}" width="${label.width}" height="${labelHeight}" rx="7"></rect><text x="${label.x}" y="${label.y}" text-anchor="${label.anchor}">${svgTextLines(labelLines, label.x, label.y, label.anchor)}</text></g><title>${escapeHtml(location.name)} · ${escapeHtml(location.summary)}</title></g>`;
  }).join("");
  const firstEvent = journey[state.mapStep];
  const firstPoint = firstEvent?.location_entity_id !== null ? points.get(firstEvent.location_entity_id) : null;
  const initialMarker = firstPoint ? `transform="translate(${firstPoint.x} ${firstPoint.y})"` : "hidden";
  const initials = [...(state.overview.protagonist?.name || "主")].slice(-1).join("");
  const controls = journey.length ? `<div class="journey-controls"><button id="map-prev" class="button button-quiet" type="button">上一步</button><button id="map-play" class="button button-primary" type="button">播放编年</button><button id="map-next" class="button button-quiet" type="button">下一步</button><input id="map-step-slider" type="range" min="0" max="${journey.length - 1}" value="${state.mapStep}" aria-label="选择故事编年步骤"><strong id="map-step-count">第 ${state.mapStep + 1} 步 · 共 ${journey.length} 步</strong><label class="playback-speed-label" for="map-playback-speed">速度</label><select id="map-playback-speed" aria-label="播放速度"><option value="0.5" ${state.mapPlaybackSpeed === 0.5 ? "selected" : ""}>0.5×</option><option value="1" ${state.mapPlaybackSpeed === 1 ? "selected" : ""}>1×</option><option value="1.5" ${state.mapPlaybackSpeed === 1.5 ? "selected" : ""}>1.5×</option><option value="2" ${state.mapPlaybackSpeed === 2 ? "selected" : ""}>2×</option></select><button id="map-route-scope" class="button button-quiet route-scope" type="button">${state.mapShowFullRoute ? "只看当前附近" : "显示完整路线"}</button></div>` : "";
  const directionalKinds = new Set(["north", "south", "east", "west", "northeast", "northwest", "southeast", "southwest", "upstream", "downstream"]);
  const directionalCount = geographyRelations.filter((relation) => directionalKinds.has(relation.relative_position)).length;
  const containmentCount = geographyRelations.filter((relation) => ["inside", "contains"].includes(relation.relative_position)).length;
  const directionCoverage = directionalCount / Math.max(1, locations.length);
  const projectionCount = [...points.values()].filter((point) => point.source === "stable_topology_projection").length;
  const regionCoverage = state.mapLayout?.region_coverage || {};
  const regionCoverageNote = state.mapPresentation === "atlas"
    ? ` 当前共 ${Number(regionCoverage.generated_region_count || 0)} 个语义区域，已归区 ${Number(regionCoverage.assigned_place_count || 0)}/${Number(regionCoverage.total_place_count || locations.length)} 个地点${Number(regionCoverage.unassigned_place_count || 0) ? `，${Number(regionCoverage.unassigned_place_count)} 个待归区` : ""}`
    : "";
  const positionNote = directionalCount
    ? `已使用 ${directionalCount} 条原文方向关系约束方位；${projectionCount} 个地点只使用稳定拓扑坐标；${regionCoverageNote}`
    : containmentCount
      ? `原文没有东南西北坐标；地图保持故事拓扑，三维纵深只使用 ${containmentCount} 条包含关系；${regionCoverageNote}`
      : routeTopology.length
        ? `原文没有东南西北坐标；地图根据 ${routeTopology.length} 条移动连接形成稳定拓扑，不把坐标冒充真实方位；${regionCoverageNote}`
        : `原文尚未提供可核验方位；地点使用稳定拓扑投影，坐标不代表真实方向；${regionCoverageNote}`;
  const volumeModes = state.mapMode === "3d" ? `<div class="segmented-control map-volume-modes" aria-label="三维区域显示"><button class="map-volume-mode${state.map3DVolumeMode === "shell" ? " active" : ""}" data-volume-mode="shell" type="button">外壳</button><button class="map-volume-mode${state.map3DVolumeMode === "section" ? " active" : ""}" data-volume-mode="section" type="button">剖面</button><button class="map-volume-mode${state.map3DVolumeMode === "layer" ? " active" : ""}" data-volume-mode="layer" type="button">当前层</button></div>` : "";
  const mapValidated = state.mapLayout?.validation_state !== "invalid";
  const mapModes = `<div class="map-view-toolbar"><div class="map-toolbar-groups"><div class="segmented-control" aria-label="地图表现"><button class="map-presentation${state.mapPresentation === "atlas" ? " active" : ""}" data-presentation="atlas" type="button" ${mapValidated ? "" : "disabled"}>语义世界图</button><button class="map-presentation${state.mapPresentation === "evidence" ? " active" : ""}" data-presentation="evidence" type="button">证据逻辑图</button></div><div class="segmented-control" aria-label="地图维度"><button class="map-mode${state.mapMode === "2d" ? " active" : ""}" data-mode="2d" type="button">2D</button><button class="map-mode${state.mapMode === "3d" ? " active" : ""}" data-mode="3d" type="button">3D</button></div>${volumeModes}</div><span>${state.mapMode === "3d" ? "拖动旋转，滚轮缩放；区域体积只使用有证据的包含层级" : state.mapPresentation === "atlas" ? "实线是原文包含区域；虚线是故事组织区域" : "只显示能够回到原文的方向、包含和移动关系"}</span></div>`;
  const viewportControls = `<div class="map-viewport-tools"><details class="map-view-menu"><summary>视角 · ${escapeHtml(mapCameraLabel())}</summary><div class="map-view-menu-popover" aria-label="地图视角"><button id="map-fit-world" data-camera-mode="world" aria-pressed="${state.mapCameraMode === "world"}" type="button">全世界</button><button id="map-fit-region" data-camera-mode="region" aria-pressed="${state.mapCameraMode === "region"}" type="button">当前区域</button><button id="map-fit-step" data-camera-mode="step" aria-pressed="${state.mapCameraMode === "step"}" type="button">当前步骤</button><button id="map-fit-follow" data-camera-mode="follow" aria-pressed="${state.mapCameraMode === "follow"}" type="button">跟随任务</button><button id="map-zoom-reset" type="button">恢复当前视角</button></div></details><div class="map-viewport-controls" aria-label="地图缩放"><button id="map-zoom-out" type="button" aria-label="缩小地图">−</button><button id="map-zoom-in" type="button" aria-label="放大地图">＋</button></div></div>`;
  const mapControlDeck = `<div class="map-control-deck">${mapModes}${viewportControls}</div>`;
  const mapCanvas = state.mapMode === "3d"
    ? `<div class="map-3d-shell"><div id="map-3d" class="map-3d" role="img" aria-label="可旋转、缩放并逐步播放的三维故事地图"></div><div id="map-3d-labels" class="map-3d-labels" aria-hidden="true"></div><div class="map-3d-axis" aria-hidden="true">${directionalCount ? "<span>北 ↑</span>" : "<span>平面方位未知</span>"}<span>纵深＝有证据的包含层级</span>${directionalCount ? "<span>东 →</span>" : "<span>拓扑坐标</span>"}</div></div>`
    : `<svg class="map-svg" viewBox="0 0 900 470" role="img" aria-label="可拖动、缩放并逐步播放的二维故事地图"><defs><marker id="route-arrow" markerUnits="userSpaceOnUse" markerWidth="3.2" markerHeight="3.2" refX="2.8" refY="1.6" orient="auto"><path d="M0,0 L3.2,1.6 L0,3.2 z" fill="context-stroke"></path></marker></defs>${paper}${semanticRegions}${geography}${topology}${routes}${nodes}<g id="journey-avatar" class="journey-avatar" ${initialMarker}><circle r="15"></circle><text y="4">${escapeHtml(initials)}</text></g></svg>`;
  const mapValidationNotice = mapValidated ? "" : `<div class="error-recovery map-validation-notice" role="alert"><strong>语义世界图暂未显示</strong><span>地点关系与当前布局不一致；系统已切换到证据逻辑图，待处理事项中可以查看失败关系</span></div>`;
  $("#view-panel").innerHTML = `${panelHead("逻辑地图与故事编年", "二维和三维共用同一个故事步骤、地点证据和播放状态", legend)}${mapValidationNotice}<p class="map-position-note">${escapeHtml(positionNote)}</p>${protagonistPicker}${controls}<div class="journey-layout"><div class="map-stage">${mapControlDeck}${mapCanvas}</div><aside class="map-context-rail" aria-label="地图编年与地点信息"><div class="map-rail-tabs" role="tablist"><button class="map-rail-tab${state.mapRailTab === "chronology" ? " active" : ""}" data-tab="chronology" role="tab" type="button">故事编年</button><button class="map-rail-tab${state.mapRailTab === "location" ? " active" : ""}" data-tab="location" role="tab" type="button">地点档案</button></div><div id="map-chronology-panel" class="map-rail-panel" ${state.mapRailTab === "chronology" ? "" : "hidden"}><section id="map-event-card" class="map-event-card" aria-live="polite"></section><nav id="map-chronology-list" class="map-chronology-list" aria-label="完整故事编年"></nav></div><section id="map-location-panel" class="map-location-panel map-rail-panel" aria-live="polite" ${state.mapRailTab === "location" ? "" : "hidden"}></section></aside></div>`;
  bindProtagonistPicker();
  bindMapRailTabs();
  if (state.mapMode === "3d") {
    createMapGraph3D(locations, geographyRelations, routeTopology, journey, routeByEventId, points);
  } else {
    bindMapViewport();
  }
  $$(".map-mode").forEach((button) => button.addEventListener("click", () => {
    const nextMode = button.dataset.mode;
    if (nextMode === state.mapMode) return;
    persistMapCameraState();
    state.mapMode = nextMode;
    window.localStorage.setItem("novel-atlas-map-mode", nextMode);
    state.mapViewport = null;
    disposeMapGraph({ persist: false });
    renderMap();
  }));
  $$(".map-presentation").forEach((button) => button.addEventListener("click", () => {
    const nextPresentation = button.dataset.presentation;
    if (nextPresentation === state.mapPresentation) return;
    persistMapCameraState();
    state.mapPresentation = nextPresentation;
    window.localStorage.setItem("novel-atlas-map-presentation", nextPresentation);
    state.mapViewport = null;
    disposeMapGraph({ persist: false });
    renderMap();
  }));
  $$(".map-volume-mode").forEach((button) => button.addEventListener("click", () => {
    const nextMode = button.dataset.volumeMode;
    if (nextMode === state.map3DVolumeMode) return;
    state.map3DVolumeMode = nextMode;
    window.localStorage.setItem("novel-atlas-3d-volume-mode", nextMode);
    disposeMapGraph();
    renderMap();
  }));
  $$(".map-view-menu button").forEach((button) => button.addEventListener("click", () => {
    if (button.dataset.cameraMode) {
      state.mapCameraMode = button.dataset.cameraMode;
      window.localStorage.setItem("novel-atlas-map-camera-mode", state.mapCameraMode);
      if (state.mapCameraMode === "follow") {
        const currentEvent = storyMapSteps()[state.mapStep];
        const point = state.mapPoints?.get(Number(currentEvent?.location_entity_id));
        const plan = point ? state.mapViewportController?.planFollow?.(point, MAP_CAMERA_SAFE_MARGIN) : null;
        if (plan?.moved) state.mapViewportController?.interpolate?.(plan, 1);
        state.mapViewportController?.persist?.();
      }
      const menu = button.closest("details");
      const label = mapCameraLabel();
      menu?.querySelector("summary")?.replaceChildren(document.createTextNode(`视角 · ${label}`));
      menu?.querySelectorAll("[data-camera-mode]").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
    }
    button.closest("details")?.removeAttribute("open");
  }));
  $$(".semantic-region").forEach((regionElement) => {
    const activate = () => renderMapRegionDetails(regionElement.dataset.region);
    regionElement.addEventListener("click", activate);
    regionElement.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        activate();
      }
    });
  });
  if (!journey.length) {
    $("#map-event-card").innerHTML = emptyState("还没有连续行程", "事件需要同时包含地点和主线人物，才能在地图上逐步移动");
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
      activateMapRailTab("location");
    };
    node.addEventListener("click", activate);
    node.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") activate(); });
  });
  $("#map-prev").addEventListener("click", () => setMapStep(state.mapStep - 1));
  $("#map-next").addEventListener("click", () => setMapStep(state.mapStep + 1));
  $("#map-step-slider").addEventListener("input", (event) => setMapStep(Number(event.target.value)));
  $("#map-play").addEventListener("click", toggleMapPlayback);
  $("#map-playback-speed")?.addEventListener("change", (event) => {
    const speed = Number(event.target.value);
    if (![0.5, 1, 1.5, 2].includes(speed)) return;
    state.mapPlaybackSpeed = speed;
    window.localStorage.setItem("novel-atlas-playback-speed", String(speed));
    if (state.mapPlaybackState === "playing") startMapPlaybackSchedule();
  });
  $("#map-route-scope").addEventListener("click", () => {
    state.mapShowFullRoute = !state.mapShowFullRoute;
    $("#map-route-scope").textContent = state.mapShowFullRoute ? "只看当前附近" : "显示完整行程";
    setMapStep(state.mapStep, false);
    if (state.mapShowFullRoute) state.mapViewportController?.fitAll();
    else if (state.mapMarkerPoint) state.mapViewportController?.focus(state.mapMarkerPoint, true, true);
  });
  requestAnimationFrame(() => setMapStep(state.mapStep, false, { initial: true }));
  requestAnimationFrame(() => {
    const currentLocation = journey[state.mapStep]?.location_entity_id;
    if (currentLocation !== null && currentLocation !== undefined) renderMapLocationDetails(Number(currentLocation));
    else renderMapLocationDetails(null);
  });
}

// 三维地图复用二维地图已经验证过的方位坐标；Z 轴只表示原文明示的“包含/位于内部”，
// 不使用随机深度，也不根据行程顺序伪造地理方位；
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
    if (host) host.innerHTML = emptyState("三维组件没有载入", "已保留当前编年步骤；切换到二维地图仍可继续阅读");
    return;
  }
  const pointValues = [...points.values()];
  const centerX = (Math.min(...pointValues.map((point) => point.x)) + Math.max(...pointValues.map((point) => point.x))) / 2;
  const centerY = (Math.min(...pointValues.map((point) => point.y)) + Math.max(...pointValues.map((point) => point.y))) / 2;
  state.map3DCenter = { x: centerX, y: centerY };
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
  map3DVisibleRegions().forEach((region, index) => {
    const labelAnchor = region.label_anchor || region.centroid || region.hull?.[0];
    if (!labelAnchor) return;
    const x = (Number(labelAnchor.x) - centerX) * 0.72;
    const y = -(Number(labelAnchor.y) - centerY) * 0.72;
    const z = Number(region.containment_depth || 0) * 86 + 12;
    nodes.push({
      id: `region:${region.id}`, name: region.display_name || region.label, kind: "region", regionId: String(region.id),
      memberIds: (region.node_ids || []).map(Number), x, y, z, fx: x, fy: y, fz: z,
      importance: 0.25, active: false, focused: true, visible: true,
    });
  });
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
    .nodeLabel((node) => node.kind === "actor" ? `<strong>${escapeHtml(node.name)}</strong><br>当前故事步骤` : node.kind === "region" ? `<strong>${escapeHtml(node.name)}</strong><br>语义区域，不代表真实地理边界` : `<strong>${escapeHtml(node.name)}</strong><br>${escapeHtml(node.summary || "点击查看地点信息")}`)
    .nodeVisibility((node) => node.visible !== false)
    .nodeVal((node) => node.kind === "actor" ? 12 : node.kind === "region" ? 2.2 : node.active ? 8 : 4.5 + Math.min(3, Number(node.importance || 0) * 2))
    .nodeResolution(20)
    .nodeOpacity(1)
    .nodeColor((node) => node.kind === "actor" ? semanticPalette.current : node.kind === "region" ? semanticPalette.faction : node.active ? semanticPalette.current : node.focused === false ? "#c7cbe0" : semanticPalette.place)
    .linkLabel((link) => escapeHtml(link.label || "连接"))
    .linkVisibility((link) => link.visible !== false)
    .linkColor((link) => link.current ? semanticPalette.current : link.kind === "journey" ? (semanticPalette[link.transport] || semanticPalette.road) : link.kind === "topology" ? "#718096" : semanticPalette.place)
    .linkWidth((link) => link.current ? 2.5 : link.kind === "journey" ? 1.2 : 0.55)
    .linkOpacity(0.76)
    .linkDirectionalArrowLength((link) => link.kind === "journey" ? (link.current ? 9 : 6.5) : 0)
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
      if (node.kind === "region") {
        renderMapRegionDetails(node.regionId);
        activateMapRailTab("location");
        const members = nodes.filter((item) => item.kind === "place" && node.memberIds.includes(item.locationId));
        graph.zoomToFit(480, 90, (item) => item.kind === "place" && node.memberIds.includes(item.locationId));
        return;
      }
      const step = journey.findIndex((event) => Number(event.location_entity_id) === node.locationId);
      if (step >= 0) setMapStep(step);
      renderMapLocationDetails(node.locationId);
      activateMapRailTab("location");
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
  controls.minDistance = 8;
  controls.maxDistance = 100000;
  installMap3DRegionMeshes(graph, centerX, centerY);
  const resize = () => {
    graph.width(Math.max(320, host.clientWidth)).height(Math.max(500, Math.min(690, Math.round(window.innerHeight * 0.64))));
  };
  resize();
  state.mapGraphResizeObserver = new ResizeObserver(resize);
  state.mapGraphResizeObserver.observe(host);
  const cameraFocus = (node, animate = true, resetScale = false) => {
    if (!node) return;
    const camera = graph.camera?.();
    const currentTarget = controls.target || { x: 0, y: 0, z: 0 };
    const currentPosition = camera?.position;
    const offset = !resetScale && currentPosition
      ? { x: currentPosition.x - currentTarget.x, y: currentPosition.y - currentTarget.y, z: currentPosition.z - currentTarget.z }
      : { x: 190, y: -125, z: 260 };
    const validOffset = Math.hypot(offset.x, offset.y, offset.z) > 8 ? offset : { x: 190, y: -125, z: 260 };
    graph.cameraPosition({ x: node.x + validOffset.x, y: node.y + validOffset.y, z: node.z + validOffset.z }, { x: node.x, y: node.y, z: node.z }, animate ? 520 : 0);
  };
  const vectorSnapshot = (value, fallback = { x: 0, y: 0, z: 0 }) => ({
    x: finiteNumber(value?.x) ?? fallback.x,
    y: finiteNumber(value?.y) ?? fallback.y,
    z: finiteNumber(value?.z) ?? fallback.z,
  });
  const cameraExtent = Math.max(900, Math.abs(centerX), Math.abs(centerY), ...pointValues.map((point) => Math.max(Math.abs(point.x - centerX), Math.abs(point.y - centerY)))) * 8;
  const sanitizeCameraSnapshot = (snapshot) => {
    if (!snapshot?.position || !snapshot?.target) return null;
    const target = vectorSnapshot(snapshot.target);
    const rawPosition = vectorSnapshot(snapshot.position, { x: 0, y: 0, z: 640 });
    const delta = {
      x: rawPosition.x - target.x,
      y: rawPosition.y - target.y,
      z: rawPosition.z - target.z,
    };
    const rawDistance = Math.hypot(delta.x, delta.y, delta.z);
    const distance = Math.max(8, Math.min(100000, Number.isFinite(rawDistance) && rawDistance > 0 ? rawDistance : 640));
    const scale = distance / (rawDistance || 640);
    const clampCoordinate = (value) => Math.max(-cameraExtent, Math.min(cameraExtent, value));
    const safeTarget = { x: clampCoordinate(target.x), y: clampCoordinate(target.y), z: clampCoordinate(target.z) };
    const safePosition = {
      x: clampCoordinate(safeTarget.x + delta.x * scale),
      y: clampCoordinate(safeTarget.y + delta.y * scale),
      z: clampCoordinate(safeTarget.z + delta.z * scale),
    };
    return { position: safePosition, target: safeTarget };
  };
  const currentCameraSnapshot = () => ({
    position: vectorSnapshot(graph.camera()?.position, { x: 0, y: 0, z: 640 }),
    target: vectorSnapshot(controls.target),
  });
  const pointInSafeArea = (point, margin = MAP_CAMERA_SAFE_MARGIN) => {
    const node = nodes.find((item) => item.kind === "place" && item.mapPoint === point);
    if (!node || !host.clientWidth || !host.clientHeight) return true;
    const projected = graph.graph2ScreenCoords(node.x, node.y, node.z || 0);
    if (!projected || !Number.isFinite(projected.x) || !Number.isFinite(projected.y)) return true;
    const safeX = host.clientWidth * Math.max(0.05, Math.min(0.45, margin));
    const safeY = host.clientHeight * Math.max(0.05, Math.min(0.45, margin));
    return projected.x >= safeX && projected.x <= host.clientWidth - safeX
      && projected.y >= safeY && projected.y <= host.clientHeight - safeY;
  };
  const planFollow = (point, margin = MAP_CAMERA_SAFE_MARGIN) => {
    const start = currentCameraSnapshot();
    if (pointInSafeArea(point, margin)) return { kind: "3d", start, end: { position: { ...start.position }, target: { ...start.target } }, moved: false };
    const node = nodes.find((item) => item.kind === "place" && item.mapPoint === point);
    if (!node) return { kind: "3d", start, end: { position: { ...start.position }, target: { ...start.target } }, moved: false };
    const endTarget = { x: node.x, y: node.y, z: node.z };
    const offset = { x: start.position.x - start.target.x, y: start.position.y - start.target.y, z: start.position.z - start.target.z };
    const endPosition = { x: endTarget.x + offset.x, y: endTarget.y + offset.y, z: endTarget.z + offset.z };
    return { kind: "3d", start, end: { position: endPosition, target: endTarget }, moved: true };
  };
  const applyCameraSnapshot = (snapshot, duration = 0) => {
    const safeSnapshot = sanitizeCameraSnapshot(snapshot);
    if (!safeSnapshot) return false;
    graph.cameraPosition(safeSnapshot.position, safeSnapshot.target, duration);
    if (controls.target?.set) controls.target.set(safeSnapshot.target.x, safeSnapshot.target.y, safeSnapshot.target.z);
    controls.update?.();
    return true;
  };
  state.mapViewportController = {
    capture() { return { kind: "3d", camera: currentCameraSnapshot() }; },
    cancelTransition() { cancelMapTransition(); },
    persist() { persistMapCameraState(); },
    restore(snapshot) {
      if (!applyCameraSnapshot(snapshot?.camera, 0)) return false;
      refresh();
      return true;
    },
    planFollow,
    interpolate(plan, progress) {
      if (!plan?.start || !plan?.end) return;
      const interpolateVector = (start, end) => ({
        x: start.x + (end.x - start.x) * progress,
        y: start.y + (end.y - start.y) * progress,
        z: start.z + (end.z - start.z) * progress,
      });
      applyCameraSnapshot({ position: interpolateVector(plan.start.position, plan.end.position), target: interpolateVector(plan.start.target, plan.end.target) }, 0);
      refresh();
    },
    focus(point) {
      const node = nodes.find((item) => item.kind !== "actor" && item.mapPoint === point);
      if (node) {
        cameraFocus(node, true);
        scheduleMapCameraPersist();
      }
    },
    focusGroup(points = [], anchor = null) {
      const selected = new Set(points.filter(Boolean));
      if (selected.size) {
        graph.zoomToFit(480, 76, (node) => node.kind === "place" && selected.has(node.mapPoint));
      } else if (anchor) {
        this.focus(anchor, true);
      } else {
        graph.zoomToFit(480, 76, (node) => node.kind !== "actor" && node.focused !== false);
      }
      scheduleMapCameraPersist();
    },
    fitAll() {
      graph.zoomToFit(520, 78, (node) => node.kind !== "actor");
      scheduleMapCameraPersist();
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
    scheduleMapCameraPersist();
  };
  $("#map-zoom-in")?.addEventListener("click", () => zoomCamera(0.82));
  $("#map-zoom-out")?.addEventListener("click", () => zoomCamera(1.18));
  $("#map-zoom-reset")?.addEventListener("click", () => {
    const saved = mapCameraSnapshot("3d");
    if (!state.mapViewportController.restore(saved)) graph.zoomToFit(520, 78, (node) => node.kind !== "actor");
    persistMapCameraState();
  });
  $("#map-fit-world")?.addEventListener("click", () => {
    graph.zoomToFit(520, 88, (node) => node.kind === "place");
    scheduleMapCameraPersist();
  });
  $("#map-fit-step")?.addEventListener("click", () => {
    const currentLocationId = Number(storyMapSteps()[state.mapStep]?.location_entity_id || 0);
    const node = nodes.find((item) => item.kind === "place" && item.locationId === currentLocationId);
    if (node) {
      cameraFocus(node, true, false);
      scheduleMapCameraPersist();
    }
  });
  $("#map-fit-region")?.addEventListener("click", () => {
    const currentLocationId = Number(storyMapSteps()[state.mapStep]?.location_entity_id || 0);
    const region = (state.mapLayout?.regions || []).find((item) => (item.node_ids || []).some((id) => Number(id) === currentLocationId));
    if (region) graph.zoomToFit(520, 90, (node) => node.kind === "place" && (region.node_ids || []).some((id) => Number(id) === node.locationId));
    scheduleMapCameraPersist();
  });
  const scheduleControlsPersist = () => scheduleMapCameraPersist();
  controls.addEventListener?.("change", scheduleControlsPersist);
  controls.addEventListener?.("end", () => persistMapCameraState());
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
    const saved = mapCameraSnapshot("3d");
    if (saved && state.mapViewportController.restore(saved)) {
      // 已恢复按书籍和范围保存的相机；
    } else if (state.mapCameraMode === "world") {
      graph.zoomToFit(0, 78, (node) => node.kind !== "actor");
    } else if (state.mapCameraMode === "step" && currentNode) {
      cameraFocus(currentNode, false);
    } else {
      const currentLocationId = Number(storyMapSteps()[state.mapStep]?.location_entity_id || 0);
      const region = (state.mapLayout?.regions || []).find((item) => (item.node_ids || []).some((id) => Number(id) === currentLocationId));
      graph.zoomToFit(0, 78, (node) => node.kind === "place" && (!region || (region.node_ids || []).some((id) => Number(id) === node.locationId)));
    }
    refresh();
  }, 80);
}

function renderMap3DLabels(graph, nodes) {
  const layer = $("#map-3d-labels");
  const host = $("#map-3d");
  if (!layer || !host) return;
  const candidates = nodes
    .filter((node) => node.kind !== "actor" && node.visible !== false && (node.kind === "region" || node.focused !== false) && Number.isFinite(node.x) && Number.isFinite(node.y))
    .sort((left, right) => Number(right.active) - Number(left.active) || Number(right.importance || 0) - Number(left.importance || 0))
    .slice(0, 28);
  const occupied = [];
  const labels = [];
  for (const node of candidates) {
    const point = graph.graph2ScreenCoords(node.x, node.y, node.z || 0);
    if (!point || point.x < 12 || point.y < 12 || point.x > host.clientWidth - 12 || point.y > host.clientHeight - 12) continue;
    const labelLines = mapLabelLines(node.name, node.kind === "region" ? 170 : 150, node.kind === "region" ? 3 : 2);
    const width = Math.max(52, ...labelLines.map((line) => [...line].length * 13 + 18));
    const height = 26 + Math.max(0, labelLines.length - 1) * 15;
    const placements = [[0, 30], [0, -30], [width / 2 + 18, 0], [-width / 2 - 18, 0]];
    let chosen = null;
    for (const [dx, dy] of placements) {
      const box = { left: point.x + dx - width / 2, right: point.x + dx + width / 2, top: point.y + dy - height / 2, bottom: point.y + dy + height / 2 };
      const outside = box.left < 6 || box.right > host.clientWidth - 6 || box.top < 6 || box.bottom > host.clientHeight - 6;
      const collides = occupied.some((placed) => !(box.right < placed.left || box.left > placed.right || box.bottom < placed.top || box.top > placed.bottom));
      if (!outside && (!collides || node.active)) { chosen = { x: point.x + dx, y: point.y + dy, box }; break; }
    }
    if (!chosen) continue;
    occupied.push(chosen.box);
    labels.push(`<span class="map-3d-label${node.active ? " active" : ""}" style="left:${chosen.x}px;top:${chosen.y}px">${labelLines.map(escapeHtml).join("<br>")}</span>`);
  }
  layer.innerHTML = labels.join("");
}

function renderMapLocationDetails(locationId) {
  const panel = $("#map-location-panel");
  if (!panel || !state.overview) return;
  const location = state.overview.entities.find((item) => Number(item.id) === Number(locationId));
  if (!location) {
    panel.innerHTML = emptyState("地点信息待确认", "这个剧情步骤暂时没有可核验地点");
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
  panel.innerHTML = `<header><span class="eyebrow">地图节点</span><h3>${escapeHtml(location.name)}</h3><p>${escapeHtml(location.summary || "原文已识别这个地点，详细说明仍待补充")}</p></header><div class="map-location-columns"><section><h4>此处发生的剧情</h4>${eventItems || '<p class="muted-copy">主线人物尚未在这里发生已收录事件</p>'}</section><section><h4>方位与路线</h4>${relationItems || routeItems ? `<ul>${relationItems}${routeItems}</ul>` : '<p class="muted-copy">原文没有提供可验证的方位或通行关系</p>'}</section></div>`;
  $$(".map-location-event").forEach((button) => button.addEventListener("click", () => {
    const step = journey.findIndex((item) => Number(item.id) === Number(button.dataset.event));
    if (step >= 0) setMapStep(step);
  }));
}

function activateMapRailTab(tab) {
  state.mapRailTab = tab === "location" ? "location" : "chronology";
  $$(".map-rail-tab").forEach((button) => {
    const active = button.dataset.tab === state.mapRailTab;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  const chronology = $("#map-chronology-panel");
  const location = $("#map-location-panel");
  if (chronology) chronology.hidden = state.mapRailTab !== "chronology";
  if (location) location.hidden = state.mapRailTab !== "location";
}

function bindMapRailTabs() {
  $$(".map-rail-tab").forEach((button) => button.addEventListener("click", () => {
    activateMapRailTab(button.dataset.tab);
  }));
  const list = $("#map-chronology-list");
  list?.addEventListener("scroll", () => {
    if (list.dataset.scrollLock === "true") return;
    const journey = storyMapSteps();
    const center = Math.max(0, Math.min(journey.length - 1, Math.floor((list.scrollTop + list.clientHeight / 2) / 76)));
    if (Math.abs(center - state.mapChronologyCenter) < 24) return;
    const scrollTop = list.scrollTop;
    state.mapChronologyCenter = center;
    renderMapChronologyList(journey, state.mapStep, false);
    list.scrollTop = scrollTop;
  }, { passive: true });
}

function renderMapChronologyList(journey, activeStep, scrollToActive = true) {
  const list = $("#map-chronology-list");
  if (!list) return;
  const itemHeight = 76;
  const radius = 34;
  const center = scrollToActive ? activeStep : state.mapChronologyCenter;
  state.mapChronologyCenter = center;
  const start = Math.max(0, center - radius);
  const end = Math.min(journey.length, center + radius + 1);
  const items = journey.slice(start, end).map((event, offset) => {
    const index = start + offset;
    return `<button class="map-chronology-item${index === activeStep ? " active" : ""}" data-step="${index}" type="button"${index === activeStep ? ' aria-current="step"' : ""}><span>${index + 1}</span><strong>${escapeHtml(event.title)}</strong><small>${escapeHtml(event.temporal_value || chapterForSegment(event.first_segment))}</small></button>`;
  }).join("");
  list.dataset.scrollLock = "true";
  list.innerHTML = `<div class="map-chronology-spacer" style="height:${start * itemHeight}px"></div>${items}<div class="map-chronology-spacer" style="height:${Math.max(0, journey.length - end) * itemHeight}px"></div>`;
  $$(".map-chronology-item").forEach((button) => button.addEventListener("click", () => {
    activateMapRailTab("chronology");
    setMapStep(Number(button.dataset.step));
  }));
  requestAnimationFrame(() => {
    if (scrollToActive) list.scrollTop = Math.max(0, activeStep * itemHeight - list.clientHeight * 0.42);
    list.dataset.scrollLock = "false";
  });
}

function renderMapRegionDetails(regionId) {
  const region = (state.mapLayout?.regions || []).find((item) => String(item.id) === String(regionId));
  const panel = $("#map-location-panel");
  if (!region || !panel) return;
  const locationIds = new Set((region.node_ids || []).map(Number));
  const locations = state.overview.entities.filter((item) => item.kind === "place" && locationIds.has(Number(item.id)));
  const events = storyMapSteps().filter((item) => locationIds.has(Number(item.location_entity_id)));
  const locationButtons = locations.map((item) => `<button class="map-region-location" data-location="${item.id}" type="button"><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.summary || "原文已识别此地点")}</small></button>`).join("");
  const eventButtons = events.slice(0, 40).map((item) => `<button class="map-location-event" data-event="${item.id}" type="button"><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.temporal_value || chapterForSegment(item.first_segment))}</span></button>`).join("");
  const level = Number(region.containment_depth || 0);
  panel.innerHTML = `<header><span class="eyebrow">${region.evidence_level === "explicit" ? "原文包含区域" : "语义区域"}</span><h3>${escapeHtml(region.label)}</h3><p>包含 ${locations.length} 个当前可见地点 · 层级 ${level} · 区域轮廓只用于组织故事空间</p></header><section class="map-region-section"><h4>区域地点</h4>${locationButtons || '<p class="muted-copy">当前剧透边界内没有可显示地点</p>'}</section><section class="map-region-section"><h4>相关编年</h4>${eventButtons || '<p class="muted-copy">当前没有绑定到该区域的编年事件</p>'}</section>`;
  $$(".map-region-location").forEach((button) => button.addEventListener("click", () => renderMapLocationDetails(Number(button.dataset.location))));
  $$(".map-location-event").forEach((button) => button.addEventListener("click", () => {
    const step = storyMapSteps().findIndex((item) => Number(item.id) === Number(button.dataset.event));
    if (step >= 0) {
      activateMapRailTab("chronology");
      setMapStep(step);
    }
  }));
  activateMapRailTab("location");
}

// 地图使用视窗坐标完成滚轮缩放和空白处拖动；步骤更新只在安全区外平滑移动相机；
function bindMapViewport() {
  const svg = $(".map-svg");
  if (!svg) return;
  const bounds = state.mapBounds || { minX: 0, maxX: 900, minY: 0, maxY: 470 };
  const ratio = 900 / 470;
  const worldWidth = Math.max(900, bounds.maxX - bounds.minX);
  const maxWidth = worldWidth * 8;
  // 给镜头保留独立的安全缓冲；内容仍以真实地图边界绘制，但跟随镜头可以
  // 在边缘留出稳定的安全区，避免人物贴边时无法满足可视范围；
  const cameraPaddingX = Math.max(120, worldWidth * 0.24);
  const cameraPaddingY = Math.max(80, (bounds.maxY - bounds.minY) * 0.24);
  const cameraBounds = {
    minX: bounds.minX - cameraPaddingX,
    maxX: bounds.maxX + cameraPaddingX,
    minY: bounds.minY - cameraPaddingY,
    maxY: bounds.maxY + cameraPaddingY,
  };
  const clampAxis = (value, size, minimum, maximum) => maximum - minimum <= size
    ? (minimum + maximum - size) / 2
    : Math.max(minimum, Math.min(maximum - size, value));
  const normalizeView = (candidate) => {
    const width = Math.max(70, Math.min(maxWidth, finiteNumber(candidate?.width) || 900));
    const height = width / ratio;
    const x = clampAxis(finiteNumber(candidate?.x) ?? bounds.minX, width, cameraBounds.minX, cameraBounds.maxX);
    const y = clampAxis(finiteNumber(candidate?.y) ?? bounds.minY, height, cameraBounds.minY, cameraBounds.maxY);
    return { x, y, width, height };
  };
  const currentEvent = storyMapSteps()[state.mapStep];
  const initialPoint = state.mapPoints?.get(Number(currentEvent?.location_entity_id)) || [...(state.mapPoints?.values() || [])][0] || { x: 450, y: 235 };
  const defaultView = normalizeView({ x: initialPoint.x - 450, y: initialPoint.y - 235, width: 900 });
  const saved = mapCameraSnapshot("2d");
  const view = normalizeView(saved?.viewBox || state.mapViewport || defaultView);
  const apply = ({ persist = true } = {}) => {
    Object.assign(view, normalizeView(view));
    svg.setAttribute("viewBox", `${view.x} ${view.y} ${view.width} ${view.height}`);
    const visualScale = 900 / view.width;
    svg.classList.toggle("lod-low", visualScale < 0.42);
    svg.classList.toggle("lod-medium", visualScale >= 0.42 && visualScale < 0.82);
    svg.classList.toggle("lod-high", visualScale >= 0.82);
    state.mapViewport = { ...view };
    if (persist) scheduleMapCameraPersist();
  };
  const pointInSafeArea = (point, margin = MAP_CAMERA_SAFE_MARGIN, candidate = view) => {
    if (!point || !Number.isFinite(Number(point.x)) || !Number.isFinite(Number(point.y))) return true;
    const safeX = candidate.width * Math.max(0.05, Math.min(0.45, margin));
    const safeY = candidate.height * Math.max(0.05, Math.min(0.45, margin));
    return point.x >= candidate.x + safeX && point.x <= candidate.x + candidate.width - safeX
      && point.y >= candidate.y + safeY && point.y <= candidate.y + candidate.height - safeY;
  };
  const planFollow = (point, margin = MAP_CAMERA_SAFE_MARGIN) => {
    const start = { ...view };
    if (pointInSafeArea(point, margin, start)) return { kind: "2d", start, end: { ...start }, moved: false };
    const safeX = start.width * Math.max(0.05, Math.min(0.45, margin));
    const safeY = start.height * Math.max(0.05, Math.min(0.45, margin));
    const end = { ...start };
    if (point.x < start.x + safeX) end.x = point.x - safeX;
    else if (point.x > start.x + start.width - safeX) end.x = point.x - start.width + safeX;
    if (point.y < start.y + safeY) end.y = point.y - safeY;
    else if (point.y > start.y + start.height - safeY) end.y = point.y - start.height + safeY;
    Object.assign(end, normalizeView(end));
    return { kind: "2d", start, end, moved: end.x !== start.x || end.y !== start.y };
  };
  const zoom = (factor, clientX = null, clientY = null) => {
    const rect = svg.getBoundingClientRect();
    const px = clientX === null ? 0.5 : Math.max(0, Math.min(1, (clientX - rect.left) / Math.max(1, rect.width)));
    const py = clientY === null ? 0.5 : Math.max(0, Math.min(1, (clientY - rect.top) / Math.max(1, rect.height)));
    const next = normalizeView({
      width: view.width * factor,
      x: view.x + (view.width - view.width * factor) * px,
      y: view.y + (view.height - view.width * factor / ratio) * py,
    });
    Object.assign(view, next);
    apply();
  };
  svg.addEventListener("wheel", (event) => {
    event.preventDefault();
    zoom(Math.exp(Math.max(-240, Math.min(240, event.deltaY)) * 0.0012), event.clientX, event.clientY);
  }, { passive: false });

  let dragging = null;
  svg.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.closest(".map-node, .journey-route, .semantic-region")) return;
    cancelMapTransition();
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
    persistMapCameraState();
  };
  svg.addEventListener("pointerup", stopDragging);
  svg.addEventListener("pointercancel", stopDragging);
  $("#map-zoom-in")?.addEventListener("click", () => zoom(0.82));
  $("#map-zoom-out")?.addEventListener("click", () => zoom(1.18));
  $("#map-zoom-reset")?.addEventListener("click", () => {
    Object.assign(view, defaultView);
    apply();
    persistMapCameraState();
  });
  state.mapViewportController = {
    capture() { return { kind: "2d", viewBox: { ...view } }; },
    cancelTransition() { cancelMapTransition(); },
    persist() { persistMapCameraState(); },
    restore(snapshot) {
      if (!snapshot?.viewBox) return false;
      Object.assign(view, normalizeView(snapshot.viewBox));
      apply({ persist: false });
      return true;
    },
    planFollow,
    interpolate(plan, progress) {
      if (!plan?.start || !plan?.end) return;
      Object.assign(view, normalizeView({
        x: plan.start.x + (plan.end.x - plan.start.x) * progress,
        y: plan.start.y + (plan.end.y - plan.start.y) * progress,
        width: plan.start.width,
      }));
      apply({ persist: false });
    },
    isPointInSafeArea(point, margin = MAP_CAMERA_SAFE_MARGIN) { return pointInSafeArea(point, margin); },
    focus(point, force = false, resetScale = false) {
      if (!point) return;
      if (resetScale) Object.assign(view, normalizeView({ ...defaultView, x: view.x, y: view.y }));
      if (!force && pointInSafeArea(point, 0.2)) return;
      Object.assign(view, normalizeView({ ...view, x: point.x - view.width / 2, y: point.y - view.height / 2 }));
      apply();
      persistMapCameraState();
    },
    focusGroup(points, anchor = null) {
      const available = (points || []).filter((point) => point && Number.isFinite(point.x) && Number.isFinite(point.y));
      if (!available.length) {
        if (anchor) this.focus(anchor, true);
        return;
      }
      const minX = Math.min(...available.map((point) => point.x));
      const maxX = Math.max(...available.map((point) => point.x));
      const minY = Math.min(...available.map((point) => point.y));
      const maxY = Math.max(...available.map((point) => point.y));
      const requiredWidth = Math.max(620, maxX - minX + 240, (maxY - minY + 180) * ratio);
      Object.assign(view, normalizeView({ width: requiredWidth, x: (minX + maxX - requiredWidth) / 2, y: (minY + maxY - requiredWidth / ratio) / 2 }));
      apply();
      persistMapCameraState();
    },
    fitAll() {
      const width = Math.max(900, bounds.maxX - bounds.minX, (bounds.maxY - bounds.minY) * ratio);
      Object.assign(view, normalizeView({ width, x: (bounds.minX + bounds.maxX - width) / 2, y: (bounds.minY + bounds.maxY - width / ratio) / 2 }));
      apply();
      persistMapCameraState();
    },
  };
  $("#map-fit-world")?.addEventListener("click", () => state.mapViewportController.fitAll());
  $("#map-fit-step")?.addEventListener("click", () => {
    const event = storyMapSteps()[state.mapStep];
    const point = state.mapPoints?.get(Number(event?.location_entity_id));
    if (point) state.mapViewportController.focus(point, true, false);
  });
  $("#map-fit-region")?.addEventListener("click", () => {
    const event = storyMapSteps()[state.mapStep];
    const locationId = Number(event?.location_entity_id || 0);
    const region = (state.mapLayout?.regions || []).find((item) => (item.node_ids || []).some((id) => Number(id) === locationId));
    if (!region) {
      const point = state.mapPoints?.get(locationId);
      if (point) state.mapViewportController.focus(point, true, true);
      return;
    }
    const regionPoints = (region.node_ids || []).map((id) => state.mapPoints?.get(Number(id))).filter(Boolean);
    state.mapViewportController.focusGroup(regionPoints, state.mapPoints?.get(locationId));
  });
  apply({ persist: false });
}

function syncMap3DStep(event, visibleLocationIds, step, animate) {
  const graph = state.mapGraph;
  const nodes = state.map3DNodes;
  const links = state.map3DLinks;
  const actor = state.map3DActor;
  if (state.mapMode !== "3d" || !graph || !nodes || !links || !actor) return;
  const currentLocationId = event.location_entity_id === null ? null : Number(event.location_entity_id);
  const emphasis = mapRegionEmphasis(currentLocationId, currentLocationId !== null);
  nodes.forEach((node) => {
    if (node.kind === "actor") return;
    if (node.kind === "region") {
      const status = emphasis.status(node.regionId);
      node.active = status === "current";
      node.regionEmphasis = status;
      node.focused = true;
      node.visible = state.mapPresentation === "atlas";
      return;
    }
    node.active = currentLocationId !== null && node.locationId === currentLocationId;
    node.visible = true;
    node.focused = state.mapShowFullRoute || visibleLocationIds.has(node.locationId);
  });
  state.map3DRegionGroup?.children?.forEach((mesh) => {
    const region = (state.mapLayout?.regions || []).find((item) => String(item.id) === String(mesh.userData?.regionId));
    if (!region || !mesh.material) return;
    const status = emphasis.status(region.id);
    mesh.userData.active = status === "current";
    mesh.userData.emphasis = status;
    mesh.material.opacity = status === "current" ? (String(region.kind).startsWith("evidence_") ? 0.48 : 0.38)
      : status === "ancestor" ? 0.22
        : status === "last" ? 0.13
          : (String(region.kind).startsWith("evidence_") ? 0.08 : 0.04);
    mesh.material.needsUpdate = true;
  });
  if (state.map3DVolumeMode !== "shell" && state.map3DCenter) {
    disposeMap3DRegionMeshes();
    installMap3DRegionMeshes(graph, state.map3DCenter.x, state.map3DCenter.y);
  }
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
    state.map3DTargetPoint = null;
    actor.visible = false;
    graph.refresh();
    return;
  }
  actor.visible = true;
  state.map3DTargetPoint = { x: target.x, y: target.y, z: target.z + 34 };
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
  const duration = 720 / state.mapPlaybackSpeed;
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

function readMapMarkerPoint() {
  const marker = $("#journey-avatar");
  const transform = marker?.getAttribute("transform") || "";
  const match = transform.match(/translate\(\s*([-\d.]+)[,\s]+([-\d.]+)\s*\)/);
  return match ? { x: Number(match[1]), y: Number(match[2]) } : null;
}

function setMapMarkerPoint(point) {
  if (!point) return;
  const marker = $("#journey-avatar");
  marker?.removeAttribute("hidden");
  marker?.setAttribute("transform", `translate(${point.x} ${point.y})`);
  state.mapMarkerPoint = { x: point.x, y: point.y };
}

function setMapMarkerPoint3D(point) {
  const actor = state.map3DActor;
  if (!actor || !point) return;
  actor.visible = true;
  actor.x = point.x;
  actor.y = point.y;
  actor.z = point.z;
  actor.fx = point.x;
  actor.fy = point.y;
  actor.fz = point.z;
  state.mapMarkerPoint3D = { x: point.x, y: point.y, z: point.z };
  state.mapGraph?.refresh?.();
}

function startMapStepTransition(target, animate = true, { preserveCamera = false } = {}) {
  cancelMapTransition();
  if (!target) return;
  const marker = $("#journey-avatar");
  const actor = state.map3DActor;
  const start2D = state.mapMarkerPoint || readMapMarkerPoint() || target;
  const target3D = state.map3DTargetPoint;
  const start3D = state.mapMarkerPoint3D || (actor ? { x: actor.x, y: actor.y, z: actor.z } : target3D);
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const controller = state.mapViewportController;
  const cameraPlan = !preserveCamera && state.mapCameraMode !== "world"
    ? controller?.planFollow?.(target, MAP_CAMERA_SAFE_MARGIN) : null;
  // 步骤切换可能紧跟在缩放或拖动之后；先把当前视角同步写入记录，
  // 避免防抖窗口内的旧视角覆盖用户刚刚完成的操作；
  persistMapCameraState();
  const same2D = !start2D || (start2D.x === target.x && start2D.y === target.y);
  const same3D = !start3D || !target3D || (start3D.x === target3D.x && start3D.y === target3D.y && start3D.z === target3D.z);
  const markerMoving = state.mapMode === "2d" ? !same2D : !same3D;
  const shouldAnimate = Boolean(animate && !reduceMotion && (markerMoving || cameraPlan?.moved));
  if (!shouldAnimate) {
    if (state.mapMode === "2d") setMapMarkerPoint(target);
    if (state.mapMode === "3d" && target3D) setMapMarkerPoint3D(target3D);
    if (cameraPlan?.moved) controller?.interpolate?.(cameraPlan, 1);
    persistMapCameraState();
    return;
  }
  const runId = ++state.mapTransitionRunId;
  const started = performance.now();
  const duration = 720 / state.mapPlaybackSpeed;
  state.mapTransition = { runId, started, duration, cameraPlan };
  const frame = (now) => {
    if (runId !== state.mapTransitionRunId || state.view !== "map") return;
    const progress = Math.min(1, (now - started) / duration);
    const eased = 1 - Math.pow(1 - progress, 3);
    if (state.mapMode === "2d" && marker && start2D) {
      setMapMarkerPoint({
        x: start2D.x + (target.x - start2D.x) * eased,
        y: start2D.y + (target.y - start2D.y) * eased,
      });
    }
    if (state.mapMode === "3d" && target3D && start3D) {
      setMapMarkerPoint3D({
        x: start3D.x + (target3D.x - start3D.x) * eased,
        y: start3D.y + (target3D.y - start3D.y) * eased,
        z: start3D.z + (target3D.z - start3D.z) * eased,
      });
    }
    if (cameraPlan?.moved) controller?.interpolate?.(cameraPlan, eased);
    if (progress < 1) {
      state.mapAnimationFrame = requestAnimationFrame(frame);
    } else {
      if (state.mapMode === "2d") setMapMarkerPoint(target);
      if (state.mapMode === "3d" && target3D) setMapMarkerPoint3D(target3D);
      state.mapAnimationFrame = null;
      state.mapTransition = null;
      persistMapCameraState();
    }
  };
  state.mapAnimationFrame = requestAnimationFrame(frame);
}

function setMapStep(nextStep, animate = true, options = {}) {
  const journey = storyMapSteps();
  if (!journey.length || state.view !== "map") return;
  const step = Math.max(0, Math.min(Number(nextStep), journey.length - 1));
  const event = journey[step];
  // 只有当前事件明确给出地点时才显示人物标记；地点未知时保留上一个真实坐标作为
  // 下一次移动的起点，但隐藏标记，避免把上一地点冒充为当前地点；
  const target = event.location_entity_id !== null ? state.mapPoints?.get(event.location_entity_id) : null;
  cancelMapTransition();
  state.mapStep = step;
  const focusEvents = journey.slice(Math.max(0, step - 5), Math.min(journey.length, step + 4));
  const visibleLocationIds = new Set(
    focusEvents.filter((item) => item.location_entity_id !== null).map((item) => Number(item.location_entity_id)),
  );
  // 连续数个事件都缺少地点时，保留前后最近的已知地点作为地图参照，但人物标记仍然隐藏；
  // 这样可以说明故事从哪里来、可能往哪里去，同时不会把参照地点误报成当前地点；
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
  const regionEmphasis = syncMap2DRegions(event.location_entity_id);
  syncMap3DStep(event, visibleLocationIds, step, animate);
  if (target) {
    startMapStepTransition(target, animate, { preserveCamera: Boolean(options.initial) });
  } else if (state.mapMode === "2d") {
    cancelMapTransition();
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
  $("#map-step-count").textContent = `第 ${step + 1} 步 · 共 ${journey.length} 步`;
  $("#map-prev").disabled = step === 0;
  $("#map-next").disabled = step === journey.length - 1;
  $("#map-next").textContent = step === journey.length - 1 ? "已到末步" : "下一步";
  const participants = [...new Set(event.participants.map((person) => person.name))].join("、") || "未标明";
  const history = event.location_entity_id === null
    ? [event]
    : journey.filter((item) => Number(item.location_entity_id) === Number(event.location_entity_id));
  const leg = (state.overview.routes || []).find((route) => Number(route.event_id) === Number(event.id));
  const displayedLocation = event.location_name || "地点待确认";
  const currentRegion = (state.mapLayout?.regions || []).find((region) => String(region.id) === String(regionEmphasis.primaryId));
  const displayedRegion = currentRegion?.display_name || currentRegion?.label || (event.location_entity_id === null && regionEmphasis.lastId ? "地点未知，上次确认区域" : "区域待确认");
  const pathStatus = event.location_entity_id === null
    ? "原文没有确认当前地点，人物标记暂时隐藏"
    : leg?.gap_status === "unknown_path" ? "原文路径有缺口，节点仍完整保留" : "路线连续";
  $("#map-event-card").innerHTML = `<span class="eyebrow">编年第 ${step + 1} 步 · ${escapeHtml(chapterForSegment(event.first_segment))}</span><h3>${escapeHtml(event.title)}</h3><p>${escapeHtml(eventNarrativeText(event))}</p><button id="map-evidence" class="button button-quiet full" type="button">打开这一步的原文</button><div class="map-event-facts map-event-essential"><span><b>当前地点</b>${escapeHtml(displayedLocation)}</span><span><b>在场人物</b>${escapeHtml(participants)}</span><span><b>当前区域</b>${escapeHtml(displayedRegion)}</span></div><details class="map-event-more"><summary>时间、交通与路径</summary><div class="map-event-facts"><span><b>故事时间</b>${escapeHtml(event.temporal_value || "时间未知")}</span><span><b>交通方式</b>${escapeHtml(transportLabels[leg?.transport || event.transport] || leg?.transport || event.transport || "未说明")}</span><span><b>路径状态</b>${escapeHtml(pathStatus)}</span></div></details><div class="location-history"><strong>${event.location_entity_id === null ? "当前故事步骤" : `此地共发生 ${history.length} 个编年事件`}</strong>${history.map((item) => `<button class="map-history-step" data-event="${item.id}" type="button">${escapeHtml(item.temporal_value || "时间未知")} · ${escapeHtml(item.title)}</button>`).join("")}</div>`;
  loadStoryContext(event, Number(state.overview?.book?.id || state.bookId));
  $("#map-evidence")?.addEventListener("click", () => openEventSource(event));
  $$(".map-history-step").forEach((button) => button.addEventListener("click", () => {
    const historyStep = journey.findIndex((item) => Number(item.id) === Number(button.dataset.event));
    if (historyStep >= 0) setMapStep(historyStep);
  }));
  renderMapChronologyList(journey, step, true);
  if (step === journey.length - 1 && state.mapPlaybackState === "playing") {
    stopMapPlayback(false);
    state.mapPlaybackState = "complete";
    $("#map-play").textContent = "重新播放";
  }
}

async function loadStoryContext(event, bookId = state.bookId) {
  const serial = ++state.storyContextSerial;
  try {
    const context = await api(`/api/books/${bookId}/story-context/${event.id}?through_segment=${Number($("#progress-slider").value)}`);
    if (serial !== state.storyContextSerial || Number(state.bookId) !== Number(bookId) || Number(storyMapSteps()[state.mapStep]?.id) !== Number(event.id)) return;
    const card = $("#map-event-card");
    if (!card) return;
    const useful = (context.items || []).filter((item) => String(item.value || "").trim()).slice(0, 5);
    const systems = (context.systems || []).slice(0, 2);
    const section = document.createElement("section");
    section.className = "story-knowledge-capsule";
    section.innerHTML = `<header><strong>当前剧情需要知道</strong><span>不超过防剧透进度</span></header>${useful.length ? `<ul>${useful.map((item) => `<li><b>${escapeHtml(item.name)}</b><span>${escapeHtml(item.value)}</span></li>`).join("")}</ul>` : '<p>当前步骤还没有可读且带证据的知识说明</p>'}${systems.length ? `<div class="story-system-links">${systems.map((item) => `<button class="story-system-open" data-system="${item.id}" type="button">${escapeHtml(item.name)} · ${escapeHtml(systemStructureLabel(item.structure_type))}</button>`).join("")}</div>` : ""}${(context.missing_explanations || []).length ? `<button class="button button-quiet story-knowledge-complete" type="button">整理待补知识</button>` : ""}`;
    card.appendChild(section);
    section.querySelectorAll(".story-system-open").forEach((button) => button.addEventListener("click", () => { state.view = "systems"; renderView(); }));
    section.querySelector(".story-knowledge-complete")?.addEventListener("click", async () => {
      try {
        const result = await api(`/api/books/${state.bookId}/knowledge/complete`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ instruction: "根据当前剧情涉及对象整理需要补充的解释，所有正式事实必须带逐字原文", segment_ids: [state.overview.segments.find((item) => Number(item.ordinal) === Number(event.first_segment))?.id].filter(Boolean), provider: "auto" }) });
        toast(`已整理 ${result.candidates.length} 条候选引文，确认前不会写入正式事实`);
      } catch (error) { toast(error.message, true); }
    });
  } catch (error) {
    if (serial === state.storyContextSerial) console.warn("Story context unavailable", error);
  }
}

async function openEventSource(event) {
  // 地图只打开一个原文对话框，不再创建独立的右侧详情栏；
  try {
    const evidence = await api(`/api/evidence/event/${event.id}`);
    const source = evidence[0];
    if (!source) {
      toast("这一步还没有可打开的原文证据", true);
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
  const duration = 720 / state.mapPlaybackSpeed;
  const frame = (now) => {
    const progress = Math.min(1, (now - started) / duration);
    const eased = 1 - Math.pow(1 - progress, 3);
    const x = start.x + (target.x - start.x) * eased;
    const y = start.y + (target.y - start.y) * eased;
    marker.setAttribute("transform", `translate(${x} ${y})`);
    // 逐帧保存屏幕上的实际位置；快速切换步骤中断当前动画时，下一段会从这一帧继续走；
    state.mapMarkerPoint = progress < 1 ? { x, y } : target;
    if (progress < 1) state.mapAnimationFrame = requestAnimationFrame(frame);
    else state.mapAnimationFrame = null;
  };
  state.mapAnimationFrame = requestAnimationFrame(frame);
}

function startMapPlaybackSchedule() {
  const journey = storyMapSteps();
  if (!journey.length || state.mapPlaybackState !== "playing") return;
  const runId = ++state.mapPlaybackRunId;
  clearTimeout(state.mapTimer);
  state.mapTimer = setTimeout(() => {
    if (state.mapPlaybackState !== "playing" || state.mapPlaybackRunId !== runId || state.view !== "map") return;
    setMapStep(state.mapStep + 1);
    if (state.mapStep < journey.length - 1 && state.mapPlaybackState === "playing") startMapPlaybackSchedule();
  }, 1550 / state.mapPlaybackSpeed);
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
  startMapPlaybackSchedule();
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
  const allNotes = (state.overview.world_notes || []).filter(inStoryScope);
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
  const empty = emptyState(allNotes.length ? "没有匹配的世界信息" : "还没有世界信息", allNotes.length ? "更换关键词或分类" : "可以从原文分析生成，也可以人工创建一条明确标记的补充内容");
  $("#view-panel").innerHTML = `${panelHead("世界信息", "可搜索、分类、创建、编辑、二次生成、归档和恢复；原文事实与人工补充始终分开标记")}
    <div class="world-toolbar"><input id="world-search" class="search-input" type="search" value="${escapeHtml(query)}" placeholder="搜索规则、势力、背景或地理" aria-label="搜索世界信息"><select id="world-category" class="search-input" aria-label="筛选世界信息分类">${options}</select><button id="world-systems" class="button button-quiet" type="button">体系图谱</button><button id="world-archived" class="button button-quiet" type="button">归档记录</button></div>
    <details class="world-create"><summary>创建世界信息</summary><div class="world-create-form"><label>分类<select id="world-create-category">${categories.map((item) => `<option value="${item}">${escapeHtml(categoryLabels[item] || item)}</option>`).join("")}</select></label><label>标题<input id="world-create-title" maxlength="160" placeholder="例如：灵力修炼层级"></label><label>说明<textarea id="world-create-summary" maxlength="5000" rows="4" placeholder="写清规则、条件、限制或影响；人工内容会明确标记"></textarea></label><button id="world-create-submit" class="button button-primary" type="button">创建并继续编辑</button></div></details>
    <div id="world-archive-list"></div>${notes.length ? `<div class="card-grid">${cards}</div>` : empty}`;
  $("#world-search").addEventListener("input", (event) => renderWorld(event.target.value, $("#world-category").value));
  $("#world-category").addEventListener("change", (event) => renderWorld($("#world-search").value, event.target.value));
  $("#world-systems").addEventListener("click", () => { state.view = "systems"; $("#systems-nav").hidden = false; renderView(); });
  $("#world-create-submit").addEventListener("click", async () => {
    const title = $("#world-create-title").value.trim();
    const summary = $("#world-create-summary").value.trim();
    if (!title || !summary) {
      toast("请填写标题和说明", true);
      return;
    }
    try {
      const created = await api(`/api/books/${state.bookId}/world-notes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category: $("#world-create-category").value, title, summary }),
      });
      toast("世界信息已创建，并标记为人工补充");
      await loadOverview(Number($("#progress-slider").value), true);
      openInspector("world_note", Number(created.id));
    } catch (error) {
      toast(error.message, true);
    }
  });
  $$(".world-archive").forEach((button) => button.addEventListener("click", async () => {
    if (!await confirmAction("归档世界信息", "归档后不会出现在世界信息中，仍可从归档记录恢复", "归档")) return;
    try {
      await api(`/api/world-notes/${button.dataset.id}`, { method: "DELETE" });
      toast("世界信息已归档，可随时恢复");
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
    host.innerHTML = notes.length ? `<div class="world-archive-panel"><h3>已归档世界信息</h3>${notes.map((note) => `<div><span><strong>${escapeHtml(note.title)}</strong><small>${escapeHtml(categoryLabels[note.category] || note.category)} · ${escapeHtml(note.summary)}</small></span><button class="button button-quiet world-restore" data-id="${note.id}" type="button">恢复</button></div>`).join("")}</div>` : emptyState("没有归档记录", "归档的世界信息会出现在这里");
    $$(".world-restore").forEach((button) => button.addEventListener("click", async () => {
      try {
        await api(`/api/world-notes/${button.dataset.id}/restore`, { method: "POST" });
        toast("世界信息已经恢复");
        await loadOverview(Number($("#progress-slider").value), true);
      } catch (error) {
        toast(error.message, true);
      }
    }));
  } catch (error) {
    host.innerHTML = emptyState("归档记录读取失败", error.message);
  }
}

function systemStructureLabel(value) {
  return ({ ordered: "线性等级", hierarchical: "层级结构", partial_order: "部分可比较", network: "关联网络" })[value] || value;
}

function renderSystems() {
  const active = (state.systems || []).filter((system) => system.status !== "archived");
  const cards = active.map((system) => {
    const nodeById = new Map((system.nodes || []).map((node) => [Number(node.id), node]));
    const nodes = (system.nodes || []).map((node) => `<div class="system-node-row"><button class="system-node" data-system="${system.id}" data-node="${node.id}" type="button"><strong>${escapeHtml(node.label)}</strong><span>${node.rank_value === null || node.rank_value === undefined ? "不可凭空比较" : `排序值 ${escapeHtml(node.rank_value)}`}</span><small>${node.evidence_id ? "已有原文证据" : "人工说明，尚待补证"}</small></button><span class="system-node-actions"><button class="button button-quiet system-edit-node" data-system="${system.id}" data-node="${node.id}" type="button">编辑</button><button class="button button-danger system-archive-node" data-system="${system.id}" data-node="${node.id}" type="button">归档</button></span></div>`).join("");
    const relations = (system.relations || []).map((relation) => {
      const source = nodeById.get(Number(relation.source_node_id));
      const target = nodeById.get(Number(relation.target_node_id));
      const label = ({ higher_than: "高于", contains: "包含", reports_to: "隶属于", precedes: "先于", related: "相关" })[relation.relation_type] || relation.relation_type;
      return `<li><strong>${escapeHtml(source?.label || "未知节点")}</strong><span>${escapeHtml(label)}</span><strong>${escapeHtml(target?.label || "未知节点")}</strong>${relation.evidence_id ? '<small>原文已核验</small>' : '<small>待补证</small>'}<span class="system-relation-actions"><button class="button button-quiet system-edit-relation" data-system="${system.id}" data-relation="${relation.id}" type="button">编辑</button><button class="button button-danger system-archive-relation" data-relation="${relation.id}" type="button">归档</button></span></li>`;
    }).join("");
    const orderedClass = system.structure_type === "ordered" ? " ordered" : "";
    return `<article class="system-card${orderedClass}" data-system="${system.id}"><header><div><span class="eyebrow">${escapeHtml(systemStructureLabel(system.structure_type))}</span><h3>${escapeHtml(system.name)}</h3><p>${escapeHtml(system.description || "当前体系尚无读者说明")}</p></div><div class="system-card-actions"><button class="button button-quiet system-add-node" data-system="${system.id}" type="button">添加有证据节点</button><button class="button button-quiet system-edit" data-system="${system.id}" type="button">编辑体系</button><button class="button button-danger system-archive" data-system="${system.id}" type="button">归档体系</button></div></header><div class="system-node-track">${nodes || emptyState("体系还没有节点", "只在原文明确出现后添加，无法比较的对象会并列显示")}</div>${relations ? `<ol class="system-relations">${relations}</ol>` : ""}${(system.nodes || []).length >= 2 ? `<button class="button button-quiet system-add-relation" data-system="${system.id}" type="button">连接体系节点</button>` : ""}</article>`;
  }).join("");
  $("#view-panel").innerHTML = panelHead("体系图谱", "不同体系分别保存，找不到统一高低关系时明确显示不可比较") + `<div class="system-toolbar"><button id="system-create" class="button button-primary" type="button">新建体系</button><span>${active.length ? `${active.length} 套体系 · ${(active.flatMap((item) => item.nodes || [])).length} 个节点` : "尚未发现有证据的体系"}</span></div><div class="system-grid">${cards || emptyState("当前作品没有可确认的体系", "系统不会为了生成漂亮阶梯而虚构等级，你仍可从原文建立组织、阶层或分类体系")}</div>`;
  $("#system-create").addEventListener("click", createSystemFromUi);
  $$(".system-add-node").forEach((button) => button.addEventListener("click", () => createSystemNodeFromUi(Number(button.dataset.system))));
  $$(".system-add-relation").forEach((button) => button.addEventListener("click", () => createSystemRelationFromUi(Number(button.dataset.system))));
  $$(".system-edit").forEach((button) => button.addEventListener("click", () => editSystemFromUi(Number(button.dataset.system))));
  $$(".system-archive").forEach((button) => button.addEventListener("click", () => archiveSystemFromUi(Number(button.dataset.system))));
  $$(".system-edit-node").forEach((button) => button.addEventListener("click", () => editSystemNodeFromUi(Number(button.dataset.system), Number(button.dataset.node))));
  $$(".system-archive-node").forEach((button) => button.addEventListener("click", () => archiveSystemNodeFromUi(Number(button.dataset.node))));
  $$(".system-edit-relation").forEach((button) => button.addEventListener("click", () => editSystemRelationFromUi(Number(button.dataset.system), Number(button.dataset.relation))));
  $$(".system-archive-relation").forEach((button) => button.addEventListener("click", () => archiveSystemRelationFromUi(Number(button.dataset.relation))));
}

async function createSystemFromUi() {
  const values = await formAction({
    title: "建立一套体系",
    description: "体系可以是等级、组织树、部分顺序或只有关联的网络",
    submitLabel: "创建体系",
    fields: [
      { name: "name", label: "体系名称", required: true, placeholder: "例如 修炼境界或王室职位" },
      { name: "category", label: "体系类别", type: "select", options: [
        { value: "ability", label: "修炼或能力" }, { value: "organization", label: "组织职位" },
        { value: "social", label: "社会或政治阶层" }, { value: "item", label: "物品与技能品阶" },
        { value: "lineage", label: "血统与分类" }, { value: "other", label: "其他体系" },
      ] },
      { name: "structure_type", label: "结构形式", type: "select", options: [
        { value: "ordered", label: "明确线性等级" }, { value: "hierarchical", label: "组织树或包含" },
        { value: "partial_order", label: "部分可比较" }, { value: "network", label: "只有关联，不比较高低" },
      ] },
      { name: "description", label: "读者说明", type: "textarea", rows: 4 },
    ],
  });
  if (!values) return;
  try {
    await api(`/api/books/${state.bookId}/systems`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(values) });
    state.systems = await api(`/api/books/${state.bookId}/systems`);
    $("#systems-nav").hidden = false;
    renderSystems();
  } catch (error) { toast(error.message, true); }
}

async function createSystemNodeFromUi(systemId) {
  const values = await formAction({
    title: "添加体系节点",
    description: "节点顺序必须来自原文，证据不足时不要填写排序值",
    submitLabel: "核验并添加",
    fields: [
      { name: "label", label: "节点名称", required: true },
      { name: "description", label: "说明", type: "textarea", rows: 3 },
      { name: "rank_value", label: "排序值，可留空", type: "number", hint: "数字只用于明确可排序的体系" },
      { name: "segment_id", label: "原文章节", type: "select", required: true, options: state.overview.segments.map((item) => ({ value: item.id, label: item.chapter_title })) },
      { name: "evidence_quote", label: "逐字原文", type: "textarea", rows: 4, required: true },
    ],
  });
  if (!values) return;
  try {
    await api(`/api/systems/${systemId}/nodes`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...values, rank_value: values.rank_value === "" ? null : Number(values.rank_value), segment_id: Number(values.segment_id), evidence_quote: String(values.evidence_quote).trim() }) });
    state.systems = await api(`/api/books/${state.bookId}/systems`);
    renderSystems();
  } catch (error) { toast(error.message, true); }
}

async function createSystemRelationFromUi(systemId) {
  const system = state.systems.find((item) => Number(item.id) === systemId);
  const nodeOptions = (system?.nodes || []).map((item) => ({ value: item.id, label: item.label }));
  const values = await formAction({
    title: "连接体系节点",
    description: "无法比较时不要建立高低关系，可使用相关或保持并列",
    submitLabel: "核验并连接",
    fields: [
      { name: "source_node_id", label: "起点", type: "select", options: nodeOptions, required: true },
      { name: "relation_type", label: "关系", type: "select", options: [
        { value: "higher_than", label: "高于" }, { value: "contains", label: "包含" },
        { value: "reports_to", label: "隶属于" }, { value: "precedes", label: "先于" }, { value: "related", label: "相关" },
      ] },
      { name: "target_node_id", label: "终点", type: "select", options: nodeOptions, required: true },
      { name: "segment_id", label: "原文章节", type: "select", options: state.overview.segments.map((item) => ({ value: item.id, label: item.chapter_title })), required: true },
      { name: "evidence_quote", label: "逐字原文", type: "textarea", rows: 4, required: true },
    ],
  });
  if (!values) return;
  if (values.source_node_id === values.target_node_id) return toast("体系关系不能连接同一个节点", true);
  try {
    await api(`/api/systems/${systemId}/relations`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...values, source_node_id: Number(values.source_node_id), target_node_id: Number(values.target_node_id), segment_id: Number(values.segment_id), evidence_quote: String(values.evidence_quote).trim() }) });
    state.systems = await api(`/api/books/${state.bookId}/systems`);
    renderSystems();
  } catch (error) { toast(error.message, true); }
}

async function reloadSystems() {
  state.systems = await api(`/api/books/${state.bookId}/systems`);
  $("#systems-nav").hidden = !(state.systems || []).some((item) => item.status === "active");
  renderSystems();
}

async function editSystemFromUi(systemId) {
  const system = state.systems.find((item) => Number(item.id) === systemId);
  if (!system) return;
  const values = await formAction({
    title: "编辑体系",
    submitLabel: "保存体系",
    fields: [
      { name: "name", label: "体系名称", required: true, value: system.name },
      { name: "category", label: "体系类别", type: "select", value: system.category, options: [
        { value: "ability", label: "修炼或能力" }, { value: "organization", label: "组织职位" },
        { value: "social", label: "社会或政治阶层" }, { value: "item", label: "物品与技能品阶" },
        { value: "lineage", label: "血统与分类" }, { value: "other", label: "其他体系" },
      ] },
      { name: "structure_type", label: "结构形式", type: "select", value: system.structure_type, options: [
        { value: "ordered", label: "明确线性等级" }, { value: "hierarchical", label: "组织树或包含" },
        { value: "partial_order", label: "部分可比较" }, { value: "network", label: "只有关联，不比较高低" },
      ] },
      { name: "description", label: "读者说明", type: "textarea", rows: 4, value: system.description },
    ],
  });
  if (!values) return;
  try {
    await api(`/api/systems/${systemId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(values) });
    await reloadSystems();
  } catch (error) { toast(error.message, true); }
}

async function archiveSystemFromUi(systemId) {
  if (!await confirmAction("归档整套体系", "节点、关系和原文证据都会保留，可以通过数据记录恢复", "归档")) return;
  try {
    await api(`/api/systems/${systemId}`, { method: "DELETE" });
    await reloadSystems();
  } catch (error) { toast(error.message, true); }
}

async function editSystemNodeFromUi(systemId, nodeId) {
  const node = state.systems.find((item) => Number(item.id) === systemId)?.nodes?.find((item) => Number(item.id) === nodeId);
  if (!node) return;
  const values = await formAction({
    title: "编辑体系节点",
    description: "本次修改不会替换已经绑定的原文证据",
    submitLabel: "保存节点",
    fields: [
      { name: "label", label: "节点名称", required: true, value: node.label },
      { name: "description", label: "说明", type: "textarea", rows: 3, value: node.description },
      { name: "rank_value", label: "排序值，可留空", type: "number", value: node.rank_value ?? "" },
    ],
  });
  if (!values) return;
  try {
    await api(`/api/system-nodes/${nodeId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ label: values.label, description: values.description, rank_value: values.rank_value === "" ? null : Number(values.rank_value), clear_rank: values.rank_value === "" }) });
    await reloadSystems();
  } catch (error) { toast(error.message, true); }
}

async function archiveSystemNodeFromUi(nodeId) {
  if (!await confirmAction("归档体系节点", "与此节点相连的关系也会归档，原文证据不会删除", "归档")) return;
  try {
    await api(`/api/system-nodes/${nodeId}`, { method: "DELETE" });
    await reloadSystems();
  } catch (error) { toast(error.message, true); }
}

async function editSystemRelationFromUi(systemId, relationId) {
  const relation = state.systems.find((item) => Number(item.id) === systemId)?.relations?.find((item) => Number(item.id) === relationId);
  if (!relation) return;
  const values = await formAction({
    title: "编辑体系关系",
    description: "只修改关系说明，原文证据保持不变",
    submitLabel: "保存关系",
    fields: [{ name: "relation_type", label: "关系", type: "select", value: relation.relation_type, options: [
      { value: "higher_than", label: "高于" }, { value: "contains", label: "包含" },
      { value: "reports_to", label: "隶属于" }, { value: "precedes", label: "先于" }, { value: "related", label: "相关" },
    ] }],
  });
  if (!values) return;
  try {
    await api(`/api/system-relations/${relationId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(values) });
    await reloadSystems();
  } catch (error) { toast(error.message, true); }
}

async function archiveSystemRelationFromUi(relationId) {
  if (!await confirmAction("归档体系关系", "关系从读者视图隐藏，原文证据继续保留", "归档")) return;
  try {
    await api(`/api/system-relations/${relationId}`, { method: "DELETE" });
    await reloadSystems();
  } catch (error) { toast(error.message, true); }
}

function renderDatabase(query = state.knowledgeQuery || "", category = state.knowledgeCategory || "all") {
  state.knowledgeQuery = query;
  state.knowledgeCategory = category;
  if (Number(state.conceptsBookId) !== Number(state.bookId)) {
    $("#view-panel").innerHTML = panelHead("知识库", "正在切换到当前作品") + '<div class="loading">正在读取这本书的知识结构…</div>';
    return;
  }
  const facets = state.knowledgeFacets || { categories: [], concept_count: 0, evidence_link_count: 0, needs_classification: 0 };
  const concepts = (state.concepts || []).filter((concept) => {
    if (concept.scheme === "system") return false;
    const haystack = `${concept.preferred_label} ${concept.description} ${(concept.aliases || []).join(" ")}`.toLowerCase();
    return inStoryScope(concept) && (category === "all" || concept.category === category) && haystack.includes(query.toLowerCase());
  });
  const facetButtons = [`<button class="knowledge-facet${category === "all" ? " active" : ""}" data-category="all" type="button"><span>全部知识</span><strong>${Number(facets.concept_count || 0)}</strong></button>`, ...(facets.categories || []).map((item) => `<button class="knowledge-facet${category === item.key ? " active" : ""}" data-category="${escapeHtml(item.key)}" type="button"><span>${escapeHtml(item.label || categoryLabels[item.key] || item.key)}</span><strong>${Number(item.count || 0)}</strong></button>`)].join("");
  const conceptRows = concepts.length ? `<div class="concept-list">${concepts.map((concept) => `<button class="concept-row" data-concept="${concept.id}" type="button"><span><strong>${escapeHtml(concept.preferred_label)}</strong><span>${escapeHtml(concept.description || "尚未填写读者说明")}${concept.aliases?.length ? ` · 别名：${escapeHtml(concept.aliases.join("、"))}` : ""}</span></span><small>${escapeHtml(categoryLabels[concept.category] || concept.category)} · ${Number(concept.claim_count || 0)} 条事实 · ${Number(concept.evidence_count || 0)} 条证据</small></button>`).join("")}</div>` : emptyState("没有匹配的知识概念", "更换关键词或分类，无法自动判断的内容会保留在待归类中");
  const parentOptions = [`<option value="">不设置上位概念</option>`, ...(state.concepts || []).filter((item) => item.status === "active").map((item) => `<option value="${item.id}">${escapeHtml(item.preferred_label)}</option>`)].join("");
  $("#view-panel").innerHTML = panelHead("知识库", "原子事实、概念分类和读者说明分层保存；原文、人工内容与外部资料不会混成一种证据") + `<div class="knowledge-workspace"><aside class="knowledge-sidebar"><h3>分类</h3>${facetButtons}<details class="world-create"><summary>创建概念或文件夹</summary><div class="world-create-form"><label>分类<input id="concept-create-category" maxlength="80" value="term" placeholder="例如：skill"></label><label>名称<input id="concept-create-label" maxlength="160" placeholder="稳定、便于检索的名称"></label><label>上位概念<select id="concept-create-parent">${parentOptions}</select></label><label>别名<input id="concept-create-aliases" maxlength="500" placeholder="使用逗号分隔"></label><label>说明<textarea id="concept-create-description" rows="4" maxlength="5000"></textarea></label><button id="concept-create-submit" class="button button-primary" type="button">创建概念</button></div></details></aside><section class="knowledge-main"><div class="database-toolbar"><input id="entry-search" class="search-input" value="${escapeHtml(query)}" placeholder="搜索名称、别名、说明或证据" aria-label="搜索知识库"></div><div class="knowledge-summary-grid"><article><strong>${Number(facets.concept_count || 0)}</strong><span>知识概念</span></article><article><strong>${Number(facets.evidence_link_count || 0)}</strong><span>证据连接</span></article><article><strong>${Number(facets.needs_classification || 0)}</strong><span>待归类</span></article></div>${conceptRows}</section></div>`;
  $("#entry-search").addEventListener("input", (event) => {
    const value = event.target.value;
    state.knowledgeQuery = value;
    window.clearTimeout(state.knowledgeSearchTimer);
    if (state.view !== "database") return;
    renderDatabase(value, category);
    const search = $("#entry-search");
    search?.focus();
    search?.setSelectionRange(value.length, value.length);
  });
  $$(".knowledge-facet").forEach((button) => button.addEventListener("click", () => {
    state.knowledgeCategory = button.dataset.category;
    renderDatabase(state.knowledgeQuery || "", state.knowledgeCategory);
  }));
  $$(".concept-row").forEach((button) => button.addEventListener("click", () => openConceptDetails(Number(button.dataset.concept))));
  $("#concept-create-submit").addEventListener("click", async () => {
    const label = $("#concept-create-label").value.trim();
    const rawAliases = $("#concept-create-aliases").value.split(/[，,]/).map((item) => item.trim()).filter(Boolean);
    if (!label) return toast("请填写概念名称", true);
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
  const requestedBookId = Number(state.bookId);
  if (Number(state.conceptsBookId) !== requestedBookId) return;
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
      api(`/api/books/${requestedBookId}/knowledge-claims?concept_id=${conceptId}`),
      api(`/api/books/${requestedBookId}/knowledge-revisions?target_type=concept&target_id=${conceptId}&limit=20`),
    ]);
    if (requestSerial !== state.inspectorRequestSerial || requestedBookId !== Number(state.bookId) || requestedBookId !== Number(state.conceptsBookId)) return;
    const sourceOptions = state.overview.segments.map((segment) => `<option value="${segment.id}">${escapeHtml(segment.chapter_title)}</option>`).join("");
    const claimCards = claims.map((claim) => {
      const value = typeof claim.value === "string" ? claim.value : JSON.stringify(claim.value, null, 2);
      const originalAction = ["entity", "world_note", "entry"].includes(claim.subject_type) ? `<button class="button button-quiet knowledge-original" data-type="${escapeHtml(claim.subject_type)}" data-id="${claim.subject_id}" type="button">打开原记录</button>` : "";
      return `<article class="evidence-card knowledge-claim" data-claim="${claim.id}"><strong>${escapeHtml(knowledgePredicateLabel(claim.predicate))}</strong><p>${escapeHtml(value)}</p><small>${escapeHtml(knowledgeSourceLabel(claim.source_kind))} · ${escapeHtml(knowledgeStatusLabel(claim.status))} · ${Number(claim.evidence_count || 0)} 条证据</small><div class="review-actions">${originalAction}${claim.evidence_count ? `<button class="button button-quiet knowledge-claim-evidence" data-id="${claim.id}" type="button">查看证据</button>` : ""}<button class="button button-danger knowledge-claim-deprecate" data-id="${claim.id}" type="button">弃用</button></div><details class="record-editor"><summary>修改事实</summary><textarea class="knowledge-claim-value">${escapeHtml(value)}</textarea><select class="knowledge-claim-status"><option value="accepted" ${claim.status === "accepted" ? "selected" : ""}>正式</option><option value="parallel" ${claim.status === "parallel" ? "selected" : ""}>并列</option><option value="needs_resolution" ${claim.status === "needs_resolution" ? "selected" : ""}>待解决</option><option value="deprecated" ${claim.status === "deprecated" ? "selected" : ""}>弃用</option></select><button class="button button-primary knowledge-claim-save" data-id="${claim.id}" type="button">保存修改</button></details></article>`;
    }).join("") || '<p class="detail-summary">当前概念还没有原子事实</p>';
    const revisionCards = revisions.length ? revisions.map((item) => `<li><strong>${escapeHtml(item.action)}</strong><span>${escapeHtml(item.created_at)}</span></li>`).join("") : "<li><span>还没有人工修改记录</span></li>";
    $("#inspector-body").innerHTML = `<p class="detail-summary">${escapeHtml(concept.description || "当前概念没有读者说明")}</p><div class="detail-row"><span>分类</span><strong>${escapeHtml(categoryLabels[concept.category] || concept.category)}</strong></div><div class="detail-row"><span>别名</span><strong>${escapeHtml((concept.aliases || []).join("、") || "—")}</strong></div><div class="record-editor"><label>名称<input id="concept-edit-label" value="${escapeHtml(concept.preferred_label)}"></label><label>说明<textarea id="concept-edit-description">${escapeHtml(concept.description || "")}</textarea></label><label>别名<input id="concept-edit-aliases" value="${escapeHtml((concept.aliases || []).join("，"))}"></label><div class="review-actions"><button id="concept-save" class="button button-primary" type="button">保存概念</button><button id="concept-archive" class="button button-danger" type="button">归档概念</button></div></div><h3 class="evidence-title">原子事实</h3>${claimCards}<details class="record-editor"><summary>新增事实</summary><label>属性<input id="knowledge-claim-predicate" maxlength="120" placeholder="例如：使用条件"></label><label>值<textarea id="knowledge-claim-value"></textarea></label><label>来源<select id="knowledge-claim-source"><option value="human_note">人工说明</option><option value="original_text">原文事实</option><option value="external_fact">外部资料</option></select></label><label>原文章节<select id="knowledge-claim-segment">${sourceOptions}</select></label><label>逐字引文<textarea id="knowledge-claim-quote" maxlength="800"></textarea></label><button id="knowledge-claim-create" class="button button-primary" type="button">保存事实</button></details><details class="record-editor knowledge-history"><summary>修改记录 · ${revisions.length}</summary><ul>${revisionCards}</ul></details>`;
    $("#concept-save").addEventListener("click", async () => {
      try {
        await api(`/api/concepts/${conceptId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ preferred_label: $("#concept-edit-label").value.trim(), description: $("#concept-edit-description").value.trim(), aliases: $("#concept-edit-aliases").value.split(/[，,]/).map((item) => item.trim()).filter(Boolean) }) });
        closeInspector(); await loadOverview(Number($("#progress-slider").value), true); toast("概念已经保存");
      } catch (error) { toast(error.message, true); }
    });
    $("#concept-archive").addEventListener("click", async () => {
      if (!await confirmAction("归档知识概念", "归档概念后，事实和证据仍会保留", "归档")) return;
      try { await api(`/api/concepts/${conceptId}`, { method: "DELETE" }); closeInspector(); await loadOverview(Number($("#progress-slider").value), true); } catch (error) { toast(error.message, true); }
    });
    $("#knowledge-claim-create").addEventListener("click", async () => {
      const sourceKind = $("#knowledge-claim-source").value;
      const predicate = $("#knowledge-claim-predicate").value.trim();
      const value = $("#knowledge-claim-value").value.trim();
      if (!predicate || !value) return toast("请填写属性和值", true);
      try {
        await api(`/api/books/${requestedBookId}/knowledge-claims`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ concept_id: conceptId, predicate, value, source_kind: sourceKind, confidence: sourceKind === "original_text" ? 0.9 : 1, segment_id: sourceKind === "original_text" ? Number($("#knowledge-claim-segment").value) : null, evidence_quote: sourceKind === "original_text" ? $("#knowledge-claim-quote").value : "", qualifiers: {} }) });
        await loadOverview(Number($("#progress-slider").value), true); openConceptDetails(conceptId);
      } catch (error) { toast(error.message, true); }
    });
    $$(".knowledge-original").forEach((button) => button.addEventListener("click", () => openInspector(button.dataset.type, Number(button.dataset.id))));
    $$(".knowledge-claim-evidence").forEach((button) => button.addEventListener("click", async () => {
      const evidence = await api(`/api/evidence/knowledge_claim/${Number(button.dataset.id)}`);
      if (!evidence.length) return toast("这条事实当前没有原文证据", true);
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
  } catch (error) { if (requestSerial === state.inspectorRequestSerial && requestedBookId === Number(state.bookId)) $("#inspector-body").innerHTML = `<p class="detail-summary">${escapeHtml(error.message)}</p>`; }
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
  if (!silent) $("#view-panel").innerHTML = panelHead("协作控制", "正在读取合同、提示词、规则和运行记录") + '<div class="loading">正在建立透明执行视图…</div>';
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
    : `<div class="prompt-trial-controls">${isExtraction ? `<label>试跑章节<select id="prompt-trial-segment">${segmentOptions}</select></label><label>试跑模型<select id="prompt-trial-provider">${providerOptions}</select></label><button id="prompt-trial" class="button button-primary" type="button">只试跑这一章</button>` : '<p>这类提示词的试跑使用全书事实批次，发布前由对应回归门禁检查</p>'}<button id="prompt-promote" class="button button-quiet" type="button">通过全库门禁后提升为正式版本</button>${detail.promoted_at ? '<button id="prompt-rollback" class="button button-danger" type="button">恢复这个历史正式版本</button>' : ""}</div>`;
  return `<section class="prompt-detail"><header><div><span class="eyebrow">${escapeHtml(detail.task_label)}</span><h3>${escapeHtml(detail.version)} · ${escapeHtml(detail.status)}</h3><p>哈希 ${escapeHtml(detail.prompt_hash.slice(0, 16))} · 约 ${Number(detail.estimated_tokens).toLocaleString()} 个输入标记</p></div><button id="prompt-detail-close" class="icon-button" type="button" aria-label="关闭提示词详情">×</button></header><div class="prompt-layer-grid">${detail.layers.map((layer) => `<article><strong>${escapeHtml(layer.label)}</strong><small>${layer.key === "external_facts" ? "与原文证据隔离，不注入抽取" : `${Number(layer.text?.length || 0).toLocaleString()} 字符`}</small></article>`).join("")}</div>${detail.diff ? `<details><summary>查看与正式版本的差异</summary><pre class="prompt-code">${escapeHtml(detail.diff)}</pre></details>` : ""}<details open><summary>查看最终系统提示词</summary><pre class="prompt-code">${escapeHtml(detail.system_prompt)}</pre></details>${runtimeControls}${draftControls}<div id="prompt-action-result" class="control-result"></div></section>`;
}

function renderCollaboration() {
  if (!state.controlPlane || Number(state.controlPlaneBookId) !== Number(state.bookId)) {
    refreshControlPlane();
    return;
  }
  const control = state.controlPlane;
  const contract = control.contract;
  const promptCards = control.prompt_bundles.map((item) => `<button class="control-card prompt-open" data-task="${escapeHtml(item.task_key)}" type="button"><span>${escapeHtml(item.task_label)}</span><strong>${escapeHtml(item.version)}</strong><small>${escapeHtml(item.prompt_hash.slice(0, 12))} · 约 ${Number(item.estimated_tokens).toLocaleString()} 标记</small></button>`).join("");
  const feedback = control.collaboration.map((item) => {
    const next = item.status === "interpreted" ? (item.requires_confirmation ? "confirmed" : "implementing") : item.status === "confirmed" ? "implementing" : item.status === "implementing" ? "validating" : item.status === "validating" ? "released" : "";
    return `<article class="collaboration-item"><div><span class="control-status ${escapeHtml(item.status)}">${escapeHtml(collaborationStatusLabels[item.status] || item.status)}</span><strong>${escapeHtml(item.original_text)}</strong><p>${escapeHtml(item.interpreted_goal)}</p><small>验收：${item.acceptance.map(escapeHtml).join(" · ")}</small><small>影响：${item.impact.map(escapeHtml).join(" · ") || "尚未登记"}</small>${item.regression_case_id ? `<small>永久回归案例 #${item.regression_case_id}</small>` : ""}</div>${next ? `<button class="button button-quiet collaboration-next" data-id="${item.id}" data-status="${next}" type="button">${next === "confirmed" ? "确认理解" : next === "released" ? "确认验收并发布" : escapeHtml(collaborationStatusLabels[next])}</button>` : ""}</article>`;
  }).join("");
  const rules = control.domain_rules.map((rule) => `<article class="rule-item ${rule.active ? "" : "inactive"}"><div><strong>${escapeHtml(rule.statement)}</strong><small>${escapeHtml(rule.task_key)} · 优先级 ${rule.priority} · 版本 ${rule.version}${rule.rationale ? ` · ${escapeHtml(rule.rationale)}` : ""}</small></div><div class="benchmark-actions"><button class="button button-quiet rule-toggle" data-id="${rule.id}" data-active="${rule.active ? "false" : "true"}" type="button">${rule.active ? "停用" : "启用"}</button><button class="button button-danger rule-delete" data-id="${rule.id}" type="button">删除</button></div></article>`).join("") || '<p class="benchmark-empty">还没有用户阅读规则，当前只使用经过测试的核心提示词</p>';
  const facts = control.external_facts.map((fact) => `<article class="rule-item ${fact.active ? "" : "inactive"}"><div><strong>${escapeHtml(fact.statement)}</strong><small>来源：${escapeHtml(fact.source_label)} · 不注入原文抽取${fact.source_url ? ` · ${escapeHtml(fact.source_url)}` : ""}</small></div><div class="benchmark-actions"><button class="button button-quiet fact-toggle" data-id="${fact.id}" data-active="${fact.active ? "false" : "true"}" type="button">${fact.active ? "停用" : "启用"}</button><button class="button button-danger fact-delete" data-id="${fact.id}" type="button">删除</button></div></article>`).join("") || '<p class="benchmark-empty">没有登记作品外资料，模型只依据当前小说原文</p>';
  const routes = control.model_routes.map((route) => `<article class="route-card ${route.eligible ? "eligible" : ""}"><span>${escapeHtml(route.provider)}</span><strong>${escapeHtml(route.model)}</strong><small>${route.eligible ? "已经通过赛马" : "尚未取得自动路由资格"} · ${route.enabled ? "已启用" : "已停用"} · 优先级 ${route.priority} · 连续失败 ${route.consecutive_failures}</small><div class="route-actions"><button class="button button-quiet route-toggle" data-provider="${escapeHtml(route.provider)}" data-enabled="${route.enabled ? "false" : "true"}" type="button">${route.enabled ? "停用" : "启用"}</button>${route.consecutive_failures ? `<button class="button button-quiet route-reset" data-provider="${escapeHtml(route.provider)}" type="button">复位熔断</button>` : ""}</div></article>`).join("");
  const promptHistory = control.prompt_versions.map((version) => `<button class="version-row prompt-version-open" data-task="${escapeHtml(version.task_key)}" data-id="${version.id}" type="button"><span>${escapeHtml(version.task_key)} · ${escapeHtml(version.version)}</span><strong>${escapeHtml(version.status)}</strong><small>${escapeHtml(version.change_note || "未填写修改说明")} · ${escapeHtml(version.prompt_hash.slice(0, 12))}</small></button>`).join("");
  const runs = control.runs.map((run) => `<details class="run-item"><summary><span>${escapeHtml(run.run_kind)}</span><strong>${escapeHtml(run.provider)} · ${escapeHtml(run.model)}</strong><small>${escapeHtml(run.status)} · 输入 ${Number(run.input_tokens || 0).toLocaleString()} · 输出 ${Number(run.output_tokens || 0).toLocaleString()} · ${run.estimated_cost_usd == null ? "订阅通道不换算美元" : formatCost(run)}</small></summary><div><p>合同 ${escapeHtml(run.contract_version)} · 提示词 ${escapeHtml(run.prompt_hash.slice(0, 16))}</p><p>评估集 ${escapeHtml(run.eval_suite_version)} · 数据结构 ${escapeHtml(run.schema_version)}</p><pre class="prompt-code compact">${escapeHtml(JSON.stringify({ validation: run.validation, conflicts: run.conflicts }, null, 2))}</pre></div></details>`).join("") || '<p class="benchmark-empty">还没有使用新版运行清单发起模型任务</p>';
  const evidenceCoverage = state.overview?.quality?.evidence_coverage_percent;
  $("#view-panel").innerHTML = panelHead("分析设置与记录", "补充阅读规则、查看结果依据和本次分析记录；内部评测由系统自己维护") + `
    <div class="control-plane">
      <section class="contract-banner">
        <div><span class="eyebrow">当前分析规则 · ${escapeHtml(contract.version)}</span><h3>${escapeHtml(contract.title)}</h3><p>${escapeHtml(contract.goal)}</p></div>
        <div class="contract-metrics"><span><strong>${evidenceCoverage == null ? "—" : `${escapeHtml(evidenceCoverage)}%`}</strong>原文证据</span><span><strong>${Number(state.reviewTasks?.length || 0)}</strong>待处理事项</span><span><strong>${Number(state.overview?.cost_summary?.job_count || 0)}</strong>分析任务</span></div>
      </section>
      <div class="security-reminder"><strong>密钥轮换待办</strong><span>旧密钥曾出现在对话中；应用已经保证它们只在 Windows 加密存储中使用，但新密钥仍需在供应商账户后台生成后替换</span></div>
      <section class="control-section"><header><div><h3>反馈与验收闭环</h3><p>先保留原话，再明确系统理解和可直接检查的结果</p></div></header><details class="control-form"><summary>登记新的反馈</summary><div class="control-form-grid"><label>你的原话<textarea id="collaboration-original" rows="3"></textarea></label><label>系统应当怎样理解<textarea id="collaboration-goal" rows="3"></textarea></label><label>验收条件，每行一条<textarea id="collaboration-acceptance" rows="4"></textarea></label><label>影响范围，每行一项<textarea id="collaboration-impact" rows="4"></textarea></label><label class="benchmark-critical"><input id="collaboration-confirm" type="checkbox"> 这项内容涉及目标、核心提示词、成本或发布，需要先确认</label><button id="collaboration-save" class="button button-primary" type="button">保存理解卡片</button></div></details><div class="collaboration-list">${feedback}</div></section>
      <section class="control-section"><header><div><h3>完整提示词</h3><p>点击查看最终文本、分层来源、版本差异和单片段试跑</p></div></header><div class="control-card-grid">${promptCards}</div>${promptDetailPanel()}<details class="prompt-history"><summary>查看全部提示词版本和回滚入口</summary><div class="version-list">${promptHistory}</div></details></section>
      <div class="settings-card-grid" data-layout-contract="independent-cards"><section class="control-section settings-card"><header><h3>阅读规则</h3><p>只能写分析方法，保存后自动进入最终提示词预览</p></header><div class="inline-control-form"><textarea id="domain-rule-statement" rows="3" placeholder="请使用陈述句，例如：明确出现父母称谓时，应当检查对象能否唯一对应现有人物"></textarea><select id="domain-rule-task"><option value="all">全部任务</option><option value="extraction">片段抽取</option><option value="global_review">全书整理</option><option value="connectivity_audit">关系复审</option></select><button id="domain-rule-save" class="button button-primary" type="button">添加阅读规则</button></div><div class="rule-list">${rules}</div></section><section class="control-section settings-card"><header><h3>外部事实</h3><p>单独保存和标明来源，永远不成为小说原文证据</p></header><div class="inline-control-form"><textarea id="external-fact-statement" rows="3" placeholder="作品外资料"></textarea><input id="external-fact-source" maxlength="300" placeholder="资料来源"><input id="external-fact-url" maxlength="1000" placeholder="来源地址，可不填"><button id="external-fact-save" class="button button-primary" type="button">保存外部资料</button></div><div class="rule-list">${facts}</div></section></div>
      <section class="control-section"><header><div><h3>模型赛马与自动路由</h3><p>同一片段、同一提示词和同一结构检查；关键事实失败就不能取得资格</p></div><button id="model-race" class="button button-primary" type="button">运行一次真实单片段赛马</button></header><div class="route-grid">${routes}</div><div id="model-race-result" class="control-result"></div></section>
      <section class="control-section"><header><h3>运行清单</h3><p>ChatGPT 登录通道不伪造单次美元费用</p></header><div class="run-list">${runs}</div></section>
    </div>`;
  const controlPlane = $(".control-plane");
  if (controlPlane) {
    const pageHead = $("#view-panel .panel-head");
    if (pageHead) {
      pageHead.querySelector("h2").textContent = "分析设置与记录";
      pageHead.querySelector("p").textContent = "用简单模式补充阅读规则、查看结果依据和本次分析记录；专业选项收在高级模式";
    }
    const guide = document.createElement("section");
    guide.className = "analysis-record-guide";
    guide.innerHTML = `<article><strong>教系统怎样理解小说</strong><span>在阅读规则中写一句明确的陈述句；例如“父母称谓必须确认指向谁”</span><button class="button button-quiet guide-rule" type="button">添加阅读规则</button></article><article><strong>查看结果为什么产生</strong><span>回到人物、事件或地图；点击对象后，在详情中的“生成溯源”查看证据和提示词版本</span><button class="button button-quiet guide-result" type="button">返回当前阅读页</button></article><article><strong>查看花费与运行记录</strong><span>每次模型调用都有用途、用量、缓存和费用；订阅通道不会伪造美元金额</span><button class="button button-quiet guide-runs" type="button">查看运行记录</button></article>`;
    controlPlane.prepend(guide);
    const advancedSections = [...controlPlane.querySelectorAll(":scope > .control-section")].filter((section) => /完整提示词|模型赛马/.test(section.textContent));
    if (advancedSections.length) {
      const advanced = document.createElement("details");
      advanced.className = "advanced-control-plane";
      advanced.innerHTML = "<summary>高级模式；提示词版本和模型赛马</summary>";
      controlPlane.insertBefore(advanced, advancedSections[0]);
      advancedSections.forEach((section) => advanced.appendChild(section));
    }
    guide.querySelector(".guide-rule")?.addEventListener("click", () => [...controlPlane.querySelectorAll("h3")].find((node) => node.textContent === "阅读规则")?.scrollIntoView({ behavior: "smooth" }));
    guide.querySelector(".guide-result")?.addEventListener("click", () => { state.view = state.viewBeforeLibrary || "relationships"; renderView(); });
    guide.querySelector(".guide-runs")?.addEventListener("click", () => [...controlPlane.querySelectorAll("h3")].find((node) => node.textContent === "运行清单")?.scrollIntoView({ behavior: "smooth" }));
    guide.insertAdjacentHTML("afterend", narrativeStructureManager());
    bindNarrativeStructureManager();
  }
  bindCollaborationControls();
}

function narrativeStructureManager() {
  const structure = state.narrativeStructure;
  if (!structure) return "";
  const worlds = structure.worlds || [];
  const units = structure.units || [];
  const worldOptions = (selected) => worlds.map((world) => `<option value="${world.id}" ${Number(world.id) === Number(selected) ? "selected" : ""}>${escapeHtml(world.name)}</option>`).join("");
  const worldCards = worlds.map((world) => {
    const members = units.filter((unit) => Number(unit.world_id) === Number(world.id));
    const mergeOptions = worlds.filter((candidate) => Number(candidate.id) !== Number(world.id)).map((candidate) => `<option value="${candidate.id}">${escapeHtml(candidate.name)}</option>`).join("");
    const unitCards = members.map((unit) => `<article class="narrative-unit-card" data-unit="${unit.id}"><div><strong>${escapeHtml(unit.title)}</strong><span>第 ${Number(unit.start_segment) + 1} 至 ${Number(unit.end_segment) + 1} 个片段；${Number((unit.entity_ids || []).length)} 个关联对象</span><small>${escapeHtml((unit.evidence || []).join(""))}</small></div><details><summary>调整名称、边界或归属</summary><div class="narrative-unit-form"><label>名称<input data-unit-field="title" value="${escapeHtml(unit.title)}"></label><label>开始片段<input data-unit-field="start_segment" type="number" min="0" value="${Number(unit.start_segment)}"></label><label>结束片段<input data-unit-field="end_segment" type="number" min="0" value="${Number(unit.end_segment)}"></label><label>所属世界<select data-unit-field="world_id">${worldOptions(unit.world_id)}</select></label><div class="action-bar"><button class="button button-primary narrative-unit-save" type="button">保存调整</button><button class="button button-quiet narrative-unit-split" type="button">拆成新世界</button></div></div></details></article>`).join("");
    return `<section class="narrative-world-card" data-world="${world.id}"><header><div><strong>${escapeHtml(world.name)}</strong><span>${members.length} 个故事单元；${world.status === "confirmed" ? "已经人工确认" : "系统软分区建议"}</span></div><button class="button button-quiet narrative-world-rename" type="button">改名</button></header><div class="narrative-world-rename-form" hidden><input value="${escapeHtml(world.name)}" maxlength="160"><button class="button button-primary narrative-world-rename-save" type="button">保存名称</button></div>${unitCards || '<p class="empty-inline">这个世界暂时没有故事单元</p>'}${mergeOptions ? `<footer><label>合并到<select class="narrative-world-merge-target">${mergeOptions}</select></label><button class="button button-quiet narrative-world-merge" type="button">合并世界</button></footer>` : ""}</section>`;
  }).join("");
  return `<section class="control-section narrative-structure-manager"><header><div><h3>故事与世界整理</h3><p>短篇集、案件集和平行故事仍是同一本书；这里的分区可以随时合并、拆分或恢复系统建议</p></div><button id="narrative-structure-rebuild" class="button button-quiet" type="button">恢复系统建议</button></header><div class="narrative-world-list">${worldCards || emptyState("尚未形成故事结构", "系统会先根据篇名、人物和地点生成可撤销建议")}</div></section>`;
}

async function refreshNarrativeStructure(result = null) {
  state.narrativeStructure = result || await api(`/api/books/${state.bookId}/narrative-structure`);
  state.storyScope = { kind: "book", id: Number(state.bookId) };
  renderCollaboration();
}

function bindNarrativeStructureManager() {
  $("#narrative-structure-rebuild")?.addEventListener("click", async () => {
    if (!await confirmAction("恢复系统分区建议", "人工调整的故事边界与世界归属会被新的本地建议替换；小说原文和分析事实不会改变", "恢复建议")) return;
    try {
      const result = await api(`/api/books/${state.bookId}/narrative-structure/rebuild`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ force: true }) });
      await refreshNarrativeStructure(result);
      toast("已经恢复可撤销的系统分区建议");
    } catch (error) { toast(error.message, true); }
  });
  $$(".narrative-world-rename").forEach((button) => button.addEventListener("click", () => {
    const card = button.closest(".narrative-world-card");
    card.querySelector(".narrative-world-rename-form").hidden = false;
    card.querySelector(".narrative-world-rename-form input").focus();
  }));
  $$(".narrative-world-rename-save").forEach((button) => button.addEventListener("click", async () => {
    const card = button.closest(".narrative-world-card");
    try {
      await patchSemanticRecord("story_world", Number(card.dataset.world), "name", card.querySelector("input").value);
      await refreshNarrativeStructure();
    } catch (error) { toast(error.message, true); }
  }));
  $$(".narrative-unit-save").forEach((button) => button.addEventListener("click", async () => {
    const card = button.closest(".narrative-unit-card");
    const unitId = Number(card.dataset.unit);
    try {
      for (const field of card.querySelectorAll("[data-unit-field]")) await patchSemanticRecord("narrative_unit", unitId, field.dataset.unitField, field.value);
      await refreshNarrativeStructure();
      toast("故事单元已经更新；相关视图会沿用同一范围");
    } catch (error) { toast(error.message, true); }
  }));
  $$(".narrative-unit-split").forEach((button) => button.addEventListener("click", async () => {
    const card = button.closest(".narrative-unit-card");
    const title = card.querySelector('[data-unit-field="title"]').value.trim();
    try {
      const result = await api(`/api/books/${state.bookId}/story-worlds/split`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ unit_ids: [Number(card.dataset.unit)], name: title }) });
      await refreshNarrativeStructure(result);
      toast("这个故事单元已经拆成独立世界；仍保留在同一本书中");
    } catch (error) { toast(error.message, true); }
  }));
  $$(".narrative-world-merge").forEach((button) => button.addEventListener("click", async () => {
    const card = button.closest(".narrative-world-card");
    const targetId = Number(card.querySelector(".narrative-world-merge-target").value);
    const target = (state.narrativeStructure.worlds || []).find((world) => Number(world.id) === targetId);
    try {
      const result = await api(`/api/books/${state.bookId}/story-worlds/merge`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ world_ids: [Number(card.dataset.world), targetId], name: target?.name || "合并世界" }) });
      await refreshNarrativeStructure(result);
      toast("两个世界已经合并；所有故事单元仍然保留");
    } catch (error) { toast(error.message, true); }
  }));
}

async function patchSemanticRecord(type, id, fieldName, newValue) {
  return api(`/api/records/${type}/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ field_name: fieldName, new_value: String(newValue), reason: "人工调整故事结构" }) });
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
  if (!note) return toast("请先说明为什么修改提示词", true);
  try {
    const draft = await api(`/api/prompt-bundles/${state.promptDetail.task_key}/drafts?book_id=${state.bookId}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ core_text: $("#prompt-core-edit").value, task_text: $("#prompt-task-edit").value, change_note: note }),
    });
    toast("提示词草稿已经保存，正式任务没有受到影响");
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
  if (!await confirmAction("发布提示词", "以后新任务会使用这个版本，旧结果仍可按版本追溯", "发布")) return;
  try {
    const result = await api(`/api/prompt-bundles/${state.promptDetail.id}/promote?book_id=${state.bookId}`, { method: "POST" });
    toast(`提示词 ${result.version} 已经成为正式版本`);
    state.promptDetail = null;
    await refreshControlPlane();
  } catch (error) { toast(error.message, true); }
}

async function rollbackPromptFromUi() {
  if (!await confirmAction("恢复提示词版本", "当前正式版本会归档，所有历史仍然保留", "恢复")) return;
  try {
    const result = await api(`/api/prompt-bundles/${state.promptDetail.id}/rollback?book_id=${state.bookId}&confirmed=true`, { method: "POST" });
    toast(`已经恢复提示词 ${result.version}`);
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
    if (!original || !goal || !acceptance.length) return toast("请填写原话、系统理解和至少一条验收条件", true);
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
    if (!statement) return toast("请先写一条阅读规则", true);
    try { await api(`/api/books/${state.bookId}/domain-rules`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ statement, task_key: $("#domain-rule-task").value }) }); state.promptDetail = null; await refreshControlPlane(); } catch (error) { toast(error.message, true); }
  });
  $$(".rule-toggle").forEach((button) => button.addEventListener("click", async () => { try { await api(`/api/domain-rules/${button.dataset.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ active: button.dataset.active === "true" }) }); state.promptDetail = null; await refreshControlPlane(); } catch (error) { toast(error.message, true); } }));
  $$(".rule-delete").forEach((button) => button.addEventListener("click", async () => { if (!await confirmAction("删除阅读规则", "删除后，新分析不会再使用这条规则", "删除")) return; try { await api(`/api/domain-rules/${button.dataset.id}`, { method: "DELETE" }); state.promptDetail = null; await refreshControlPlane(); } catch (error) { toast(error.message, true); } }));
  $("#external-fact-save")?.addEventListener("click", async () => {
    const statement = $("#external-fact-statement").value.trim(); const source = $("#external-fact-source").value.trim();
    if (!statement || !source) return toast("外部资料必须同时填写内容和来源", true);
    try { await api(`/api/books/${state.bookId}/external-facts`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ statement, source_label: source, source_url: $("#external-fact-url").value.trim() }) }); await refreshControlPlane(); } catch (error) { toast(error.message, true); }
  });
  $$(".fact-toggle").forEach((button) => button.addEventListener("click", async () => { try { await api(`/api/external-facts/${button.dataset.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ active: button.dataset.active === "true" }) }); await refreshControlPlane(); } catch (error) { toast(error.message, true); } }));
  $$(".fact-delete").forEach((button) => button.addEventListener("click", async () => { if (!await confirmAction("删除外部资料", "删除后，这条资料不会再参与后续分析", "删除")) return; try { await api(`/api/external-facts/${button.dataset.id}`, { method: "DELETE" }); await refreshControlPlane(); } catch (error) { toast(error.message, true); } }));
  $$(".route-toggle").forEach((button) => button.addEventListener("click", async () => { try { await api(`/api/model-routes/${button.dataset.provider}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: button.dataset.enabled === "true" }) }); await refreshControlPlane(); } catch (error) { toast(error.message, true); } }));
  $$(".route-reset").forEach((button) => button.addEventListener("click", async () => { try { await api(`/api/model-routes/${button.dataset.provider}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reset_circuit: true }) }); await refreshControlPlane(); } catch (error) { toast(error.message, true); } }));
  $("#model-race")?.addEventListener("click", async () => {
    if (!await confirmAction("运行模型赛马", "所有可用候选模型会分析同一个片段，API 会产生费用，Codex Luna 会消耗 ChatGPT 计划额度", "开始运行")) return;
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

// 内部回归评测由后台维护；普通阅读界面不提供人工题库入口
function renderQuality() {
  const quality = state.overview.quality;
  const reviewTasks = state.reviewTasks || [];
  const severityLabels = { blocks_analysis: "阻断分析", blocks_release: "阻断发布", advisory: "建议检查" };
  const reviewTaskCenter = reviewTasks.length ? `<section class="review-task-center"><header><div><span class="eyebrow">下一步从这里开始</span><h3>待处理事项 · ${reviewTasks.length}</h3><p>每一项都会说明问题、影响、建议和处理后会更新的页面；不再把冲突当成日志悬挂</p></div><button id="review-auto-all" class="button button-primary" type="button">自动复核全部（可能计费）</button></header><div class="review-task-list">${reviewTasks.map((task) => `<article class="review-task-card ${escapeHtml(task.severity)}" data-task="${task.id}"><div class="review-task-heading"><span>${escapeHtml(severityLabels[task.severity] || "建议检查")}</span><h4>${escapeHtml(task.title)}</h4></div><dl><div><dt>发生了什么</dt><dd>${escapeHtml(task.problem)}</dd></div><div><dt>为什么重要</dt><dd>${escapeHtml(task.impact)}</dd></div><div><dt>系统建议</dt><dd>${escapeHtml(task.recommendation)}</dd></div><div><dt>处理后更新</dt><dd>${escapeHtml((task.rebuild_scope || []).join("、") || "相关结果")}</dd></div></dl><div class="review-task-actions">${task.source_type === "event_location" ? `<button class="button button-primary location-link" data-event="${task.source_id}" type="button">查看原文并确认地点</button>` : task.source_type === "merge_candidate" ? `<button class="button button-primary review-open-advanced" type="button">比较两个人物</button>` : `<button class="button button-primary review-auto-one" data-task="${task.id}" type="button">自动复核这项</button>`}<button class="button button-quiet review-task-action" data-task="${task.id}" data-action="keep_separate" type="button">${task.category === "relationship" ? "确认当前确实独立" : task.category === "location" ? "保留地点未知" : "保持当前判断"}</button><button class="button button-quiet review-task-action" data-task="${task.id}" data-action="defer" type="button">稍后处理</button></div></article>`).join("")}</div><details class="advanced-review"><summary>打开专业处理工具</summary><p>这里保留逐条合并、反转时间约束和补建关系等高级操作；一般阅读只需处理上面的任务卡</p></details></section>` : emptyState("当前没有待处理事项", "已自动解决的记录保留在检查历史中；不会继续显示为警告");
  const atlasCoverage = state.mapLayout?.region_coverage || {};
  const atlasIssues = state.mapLayout?.quality_issues || [];
  const cost = state.overview.cost_summary || {};
  const coverage = quality.evidence_coverage_percent === null ? "—" : `${quality.evidence_coverage_percent}%`;
  const costLabel = Number(cost.priced_job_count || 0) ? `$${Number(cost.estimated_cost_usd || 0).toFixed(6)}` : "暂无计价任务";
  const metricCards = [
    `<article class="metric-card"><span>已分析原文片段</span><strong>${quality.segments_processed} / ${quality.segments_total}</strong><small>全书共分成 ${quality.segments_total} 个原文片段；其中 ${quality.segments_processed} 个已有分析结果，未分析内容不会进入结论</small></article>`,
    `<article class="metric-card"><span>原文证据覆盖</span><strong>${coverage}</strong><small>${quality.facts_with_evidence}/${quality.facts_total} 条结构记录</small></article>`,
    `<article class="metric-card${reviewTasks.length ? " warning" : ""}"><span>需要处理</span><strong>${reviewTasks.length}</strong><small>只显示会影响当前书籍的问题</small></article>`,
    `<article class="metric-card"><span>累计分析费用</span><strong>${escapeHtml(costLabel)}</strong><small>${Number(cost.job_count || 0)} 次任务 · 输入 ${Number(cost.input_tokens || 0).toLocaleString()} · 输出 ${Number(cost.output_tokens || 0).toLocaleString()}</small></article>`,
    `<article class="metric-card"><span>供应商缓存命中</span><strong>${Number(cost.cache_hit_input_tokens || 0).toLocaleString()}</strong><small>另有完整请求本地复用，不产生调用</small></article>`,
  ];
  if (Number(quality.unresolved_merges || 0) > 0) {
    metricCards.push(`<article class="metric-card warning"><span>证据不足的身份</span><strong>${quality.unresolved_merges}</strong><small>系统已避免自动合并；可以按原文复核</small></article>`);
  }
  const metrics = `<div class="metric-grid quality-metrics">${metricCards.join("")}</div>`;
  const reportIssues = [
    ...quality.issues,
    ...atlasIssues.map((issue) => ({ level: issue.severity || "warning", title: issue.issue_type === "unassigned_places" ? "地图存在待归区地点" : "地图区域需要重新整理", detail: issue.message })),
  ];
  const issues = reportIssues.length ? `<div class="quality-list">${reportIssues.map((issue) => `<article class="quality-issue ${escapeHtml(issue.level)}"><strong>${escapeHtml(issue.title)}</strong><p>${escapeHtml(issue.detail)}</p></article>`).join("")}</div>` : emptyState("当前自动检查没有发现问题", "自动检查只能验证证据、重复和结构一致性，人物理解仍可通过原文证据人工核验");
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
  const modelReviewItems = unresolvedReviews.length + unresolvedLocations.length + atlasIssues.length;
  const hasProfessionalReview = Number(quality.unresolved_merges || 0) > 0 || modelReviewItems > 0 || autoConflictCount > 0;
  const resolvedHistory = resolvedConflictCount ? `<details class="conflict-history"><summary>查看 ${resolvedConflictCount} 条已处理记录</summary><div class="conflict-history-list">${resolvedIdentities.map((item) => `<article><div><strong>身份 · ${escapeHtml(item.left_name)} ↔ ${escapeHtml(item.right_name)}</strong><small>${escapeHtml(statusLabels[item.status] || item.status)} · ${escapeHtml(item.resolution_reason || item.reason)}</small></div>${["auto_separate", "rejected"].includes(item.status) ? `<div class="conflict-actions"><button class="button button-quiet merge-choice" data-keep="${item.left_entity_id}" data-remove="${item.right_entity_id}" type="button">改为合并到 ${escapeHtml(item.left_name)}</button><button class="button button-quiet merge-choice" data-keep="${item.right_entity_id}" data-remove="${item.left_entity_id}" type="button">改为合并到 ${escapeHtml(item.right_name)}</button></div>` : ""}</article>`).join("")}${resolvedContradictions.map((item) => `<article><div><strong>事实 · ${escapeHtml(item.left.label)} ↔ ${escapeHtml(item.right.label)}</strong><small>${escapeHtml(statusLabels[item.status] || item.status)} · ${escapeHtml(item.resolution_reason || item.summary)}</small></div><div class="conflict-actions"><button class="button button-quiet contradiction-action" data-id="${item.id}" data-action="contextual" type="button">改为不同情境</button><button class="button button-quiet contradiction-action" data-id="${item.id}" data-action="false_positive" type="button">改为误报</button><button class="button button-danger contradiction-action" data-id="${item.id}" data-action="quarantine" type="button">改为隔离</button></div></article>`).join("")}${resolvedTimeConstraints.map((item) => `<article><div><strong>时间 · ${escapeHtml(item.earlier_title)} → ${escapeHtml(item.later_title)}</strong><small>${escapeHtml(statusLabels[item.status] || item.status)} · ${escapeHtml(item.resolution_reason || item.reason)}</small></div><div class="conflict-actions"><button class="button button-quiet time-conflict-action" data-id="${item.id}" data-action="reverse" type="button">改为反转</button><button class="button button-danger time-conflict-action" data-id="${item.id}" data-action="reject" type="button">改为舍弃</button><button class="button button-quiet time-conflict-action" data-id="${item.id}" data-action="quarantine" type="button">改为隔离</button></div></article>`).join("")}</div></details>` : "";
  const conflictPanel = `<section class="conflict-center"><div class="conflict-center-head"><div><h3>冲突处理中心</h3><p>自动处理只使用本地规则，不调用模型、不删除原始事实；证据不足的身份保持分离，事实冲突进入隔离区，循环时间约束会被舍弃</p></div>${autoConflictCount ? `<button id="quality-auto-close" class="button button-primary" type="button">免费自动处理 ${autoConflictCount} 项</button>` : '<span class="conflict-zero">当前无需处理</span>'}</div>${identityConflicts.length ? `<section class="conflict-group"><h4>身份候选 · ${identityConflicts.length}</h4><div class="conflict-card-list">${identityConflicts.map((item) => `<article class="conflict-card"><div><strong>${escapeHtml(item.left_name)} ↔ ${escapeHtml(item.right_name)}</strong><p>${escapeHtml(item.reason)} · 把握 ${Math.round(Number(item.confidence || 0) * 100)}%</p></div><div class="conflict-actions"><button class="button button-quiet merge-choice" data-keep="${item.left_entity_id}" data-remove="${item.right_entity_id}" type="button">合并为 ${escapeHtml(item.left_name)}</button><button class="button button-quiet merge-choice" data-keep="${item.right_entity_id}" data-remove="${item.left_entity_id}" type="button">合并为 ${escapeHtml(item.right_name)}</button><button class="button button-danger merge-reject" data-id="${item.id}" type="button">保持两个身份</button></div></article>`).join("")}</div></section>` : ""}${contradictions.length ? `<section class="conflict-group"><h4>事实冲突 · ${contradictions.length}</h4><div class="conflict-card-list">${contradictions.map((item) => `<article class="conflict-card"><div><strong>${escapeHtml(item.left.label)} ↔ ${escapeHtml(item.right.label)}</strong><p>${escapeHtml(item.summary)}</p><small>${escapeHtml(item.left.summary)} ｜ ${escapeHtml(item.right.summary)}</small></div><div class="conflict-actions"><button class="button button-quiet target-button" data-type="${escapeHtml(item.left.type)}" data-id="${item.left.id}" type="button">查看左侧</button><button class="button button-quiet target-button" data-type="${escapeHtml(item.right.type)}" data-id="${item.right.id}" type="button">查看右侧</button><button class="button button-quiet contradiction-action" data-id="${item.id}" data-action="contextual" type="button">属于不同情境</button><button class="button button-quiet contradiction-action" data-id="${item.id}" data-action="false_positive" type="button">确认误报</button><button class="button button-danger contradiction-action" data-id="${item.id}" data-action="quarantine" type="button">隔离待查</button></div></article>`).join("")}</div></section>` : ""}${timeConflicts.length ? `<section class="conflict-group"><h4>时间顺序冲突 · ${timeConflicts.length}</h4><div class="conflict-card-list">${timeConflicts.map((item) => `<article class="conflict-card"><div><strong>${escapeHtml(item.earlier_title)} → ${escapeHtml(item.later_title)}</strong><p>${escapeHtml(item.reason || "该约束会造成时间循环")}</p></div><div class="conflict-actions"><button class="button button-quiet target-button" data-type="event" data-id="${item.earlier_event_id}" type="button">查看前项</button><button class="button button-quiet target-button" data-type="event" data-id="${item.later_event_id}" type="button">查看后项</button><button class="button button-quiet time-conflict-action" data-id="${item.id}" data-action="reverse" type="button">反转顺序</button><button class="button button-danger time-conflict-action" data-id="${item.id}" data-action="reject" type="button">舍弃约束</button><button class="button button-quiet time-conflict-action" data-id="${item.id}" data-action="quarantine" type="button">隔离待查</button></div></article>`).join("")}</div></section>` : ""}${!autoConflictCount ? '<p class="conflict-complete">身份、事实和时间约束没有悬挂冲突</p>' : ""}${resolvedHistory}</section>`;
  const atlasReview = atlasIssues.length ? `<div class="connectivity-resolution-list map-quality-resolution">${atlasIssues.map((item) => `<article><span><strong>${item.issue_type === "unassigned_places" ? "地点尚未归区" : "无关区域重叠"}</strong><small>${escapeHtml(item.message)}</small></span><div><button class="button button-primary quality-open-map" type="button">打开地图核对</button></div></article>`).join("")}</div>` : "";
  const reviewPanel = `<section class="quality-resolution"><div><h3>关系与地图门禁</h3><p>${modelReviewItems ? `还有 ${modelReviewItems} 项需要模型复审或人工补证；调用模型前会继续使用任务预算` : "关系与地图项目已经闭环，不需要额外模型调用"}</p></div>${unresolvedReviews.length + unresolvedLocations.length ? '<button id="quality-retry" class="button button-quiet" type="button">调用模型复审，可能计费</button>' : ""}</section>${atlasReview}${unresolvedReviews.length ? `<div class="connectivity-resolution-list">${unresolvedReviews.map((item) => `<article><span><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.reason)} · 已扫描 ${item.scanned_segment_count} 个原文片段、${item.mention_count} 次提及</small></span><div><button class="button button-quiet target-button" data-type="entity" data-id="${item.entity_id}" type="button">查看人物</button><button class="button button-quiet connectivity-link" data-review="${item.id}" data-entity="${item.entity_id}" type="button">用原文补关系</button><button class="button button-danger connectivity-isolated" data-review="${item.id}" type="button">确认确实孤立</button></div></article>`).join("")}</div>` : ""}${unresolvedLocations.length ? `<div class="connectivity-resolution-list location-resolution-list">${unresolvedLocations.map((item) => `<article><span><strong>${escapeHtml(item.event_title)}</strong><small>${escapeHtml(item.reason)} · ${escapeHtml(chapterForSegment(item.event_first_segment))}</small></span><div><button class="button button-quiet target-button" data-type="event" data-id="${item.event_id}" type="button">查看剧情</button><button class="button button-primary location-link" data-event="${item.event_id}" type="button">用原文确认地点</button></div></article>`).join("")}</div>` : ""}`;
  const topologyMetrics = `<div class="quality-topology"><span>关系已连接 <strong>${quality.connectivity_reviewed_connected}</strong></span><span>确认孤立 <strong>${quality.connectivity_confirmed_isolated}</strong></span><span>关系待处理 <strong>${Number(quality.connectivity_pending || 0) + Number(quality.connectivity_ambiguous || 0)}</strong></span><span>地点明确 <strong>${quality.location_explicit_events}</strong></span><span>位置沿用 <strong>${quality.location_inherited_events}</strong></span><span>位置未解 <strong>${quality.location_unresolved_events}</strong></span><span>区域覆盖 <strong>${Number(atlasCoverage.assigned_place_count || 0)}/${Number(atlasCoverage.total_place_count || 0)}</strong></span><span>语义区域 <strong>${Number(atlasCoverage.generated_region_count || 0)}</strong></span><span>最大重叠 <strong>${Number(atlasCoverage.overlap?.maximum_overlap_ratio_percent || 0)}%</strong></span></div>`;
  const bookReady = Number(quality.unresolved_merges || 0) === 0 && modelReviewItems === 0 && autoConflictCount === 0;
  const blockedCount = Number(quality.unresolved_merges || 0) + modelReviewItems + autoConflictCount;
  const activeTab = ["pending", "cost", "resolved"].includes(state.qualityTab) ? state.qualityTab : "pending";
  state.qualityTab = activeTab;
  const qualityTabs = `<div class="quality-tabs" role="tablist"><button class="quality-tab${activeTab === "pending" ? " active" : ""}" data-tab="pending" type="button">待处理 <span>${reviewTasks.length}</span></button><button class="quality-tab${activeTab === "cost" ? " active" : ""}" data-tab="cost" type="button">成本与运行</button><button class="quality-tab${activeTab === "resolved" ? " active" : ""}" data-tab="resolved" type="button">检查结果</button></div>`;
  const releaseSummary = `<section class="release-decision ${bookReady ? "ready" : "blocked"}"><div><span class="eyebrow">当前书籍检查</span><h3>${bookReady ? "当前没有需要你处理的问题" : `当前有 ${blockedCount} 项问题需要处理`}</h3><p>${bookReady ? "系统会继续在后台检查证据、结构和增量变化" : "处理后只重建受影响的关系、编年或地图；不会整本重跑"}</p></div><strong>${bookReady ? "状态正常" : "需要处理"}</strong></section>`;
  const professionalReviewTools = hasProfessionalReview ? `<details class="legacy-review-tools"><summary>专业处理工具</summary>${conflictPanel}${reviewPanel}</details>` : "";
  $("#view-panel").innerHTML = panelHead("质量检查", "这里只显示真正影响当前书籍的问题、处理方法和运行记录") + `<div class="quality-body">${releaseSummary}${metrics}${qualityTabs}<section class="quality-tab-panel" data-panel="pending" ${activeTab === "pending" ? "" : "hidden"}>${reviewTaskCenter}${professionalReviewTools}</section><section class="quality-tab-panel" data-panel="cost" ${activeTab === "cost" ? "" : "hidden"}>${jobHistory}${ledgerTable}</section><section class="quality-tab-panel" data-panel="resolved" ${activeTab === "resolved" ? "" : "hidden"}>${topologyMetrics}${issues}</section></div>`;
  $$(".quality-tab").forEach((button) => button.addEventListener("click", () => {
    state.qualityTab = button.dataset.tab;
    renderQuality();
  }));
  $("#quality-auto-close")?.addEventListener("click", autoCloseConflicts);
  $("#review-auto-all")?.addEventListener("click", retryQualityChecks);
  $$(".review-auto-one").forEach((button) => button.addEventListener("click", retryQualityChecks));
  $$(".review-open-advanced").forEach((button) => button.addEventListener("click", () => $(".legacy-review-tools")?.setAttribute("open", "")));
  $$(".review-task-action").forEach((button) => button.addEventListener("click", async () => {
    try {
      await api(`/api/review-tasks/${button.dataset.task}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: button.dataset.action, note: "" }) });
      await loadOverview(Number($("#progress-slider").value), true);
    } catch (error) { toast(error.message, true); }
  }));
  $("#quality-retry")?.addEventListener("click", retryQualityChecks);
  $$(".quality-open-map").forEach((button) => button.addEventListener("click", () => {
    state.view = "map";
    state.mapPresentation = "atlas";
    $(".nav-item.active")?.classList.remove("active");
    $(".nav-item[data-view='map']")?.classList.add("active");
    renderView();
  }));
  $$(".connectivity-isolated").forEach((button) => button.addEventListener("click", () => confirmConnectivityIsolated(Number(button.dataset.review))));
  $$(".connectivity-link").forEach((button) => button.addEventListener("click", () => createManualConnectivityLink(Number(button.dataset.review), Number(button.dataset.entity))));
  $$(".location-link").forEach((button) => button.addEventListener("click", () => resolveManualEventLocation(Number(button.dataset.event))));
  $$(".contradiction-action").forEach((button) => button.addEventListener("click", () => resolveContradiction(Number(button.dataset.id), button.dataset.action)));
  $$(".time-conflict-action").forEach((button) => button.addEventListener("click", () => resolveTimeConflict(Number(button.dataset.id), button.dataset.action)));
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
    toast(`本地处理完成：身份 ${summary.identity_merged + summary.identity_separated} 项，事实冲突 ${summary.contradictions_quarantined} 项，时间约束 ${summary.time_constraints_rejected} 项；费用 $0`);
    await loadOverview(Number($("#progress-slider").value), true);
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
    button.textContent = "免费自动处理未闭环冲突";
  }
}

async function retryQualityChecks() {
  const button = $("#quality-retry");
  const autoButton = $("#review-auto-all");
  if (button) {
    button.disabled = true;
    button.textContent = "正在复审未闭环项目…";
  }
  if (autoButton) {
    autoButton.disabled = true;
    autoButton.textContent = "正在复审…";
  }
  try {
    const result = await api(`/api/books/${state.bookId}/quality/retry`, { method: "POST" });
    toast(result.status === "completed" ? "质量门禁已经通过" : "自动复审已完成，剩余歧义可以人工解决");
    await loadOverview(Number($("#progress-slider").value), true);
  } catch (error) {
    toast(error.message, true);
    if (button) {
      button.disabled = false;
      button.textContent = "自动解决剩余问题";
    }
    if (autoButton) {
      autoButton.disabled = false;
      autoButton.textContent = "自动复核全部（可能计费）";
    }
  }
}

async function resolveContradiction(id, action) {
  const defaults = {
    contextual: "两条记录分别适用于不同时间、地点、身份视角或条件",
    false_positive: "两条记录可以同时成立，这一冲突属于自动检查误报",
    quarantine: "当前证据不足以裁决，先从正式结论中隔离并保留原始证据",
  };
  const values = await formAction({
    title: "处理事实冲突",
    description: "请用陈述句记录判断依据，原始记录和证据不会被删除",
    submitLabel: "保存处理结果",
    fields: [{ name: "reason", label: "判断依据", type: "textarea", value: defaults[action], rows: 4, required: true }],
  });
  if (!values) return;
  const reason = String(values.reason).trim();
  try {
    await api(`/api/contradictions/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, reason }),
    });
    toast("事实冲突已经关闭，原始记录和证据仍然保留");
    await loadOverview(Number($("#progress-slider").value), true);
  } catch (error) {
    toast(error.message, true);
  }
}

async function resolveTimeConflict(id, action) {
  const defaults = {
    reverse: "原文证据表明两件事件的先后方向应当反转",
    reject: "该顺序约束会形成循环，舍弃约束并保留两件剧情事件",
    quarantine: "当前证据不足以确定先后，暂时隔离该约束",
  };
  const values = await formAction({
    title: "处理时间顺序冲突",
    description: "说明为何反转、舍弃或隔离这条约束，剧情事件本身会继续保留",
    submitLabel: "保存时间判断",
    fields: [{ name: "reason", label: "判断依据", type: "textarea", value: defaults[action], rows: 4, required: true }],
  });
  if (!values) return;
  const reason = String(values.reason).trim();
  try {
    await api(`/api/time-conflicts/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, reason }),
    });
    toast(action === "reverse" ? "时间顺序已经反转并重新验算" : "时间约束已经关闭，剧情事件仍然保留");
    await loadOverview(Number($("#progress-slider").value), true);
  } catch (error) {
    toast(error.message, true);
  }
}

async function confirmConnectivityIsolated(reviewId) {
  const values = await formAction({
    title: "确认孤立节点",
    description: "只有核对全部提及窗口后，才能确认没有可建立关系",
    submitLabel: "确认确实孤立",
    fields: [{ name: "reason", label: "核对说明", type: "textarea", value: "已核对全部提及窗口，没有发现明确关系", rows: 4, required: true }],
  });
  if (!values) return;
  const reason = String(values.reason).trim();
  try {
    await api(`/api/connectivity-reviews/${reviewId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "confirmed_isolated", reason }),
    });
    toast("该节点已经标记为人工确认孤立");
    await loadOverview(Number($("#progress-slider").value), true);
  } catch (error) {
    toast(error.message, true);
  }
}

async function createManualConnectivityLink(reviewId, sourceEntityId) {
  const people = state.overview.entities.filter((item) => ["person", "faction"].includes(item.kind) && Number(item.id) !== sourceEntityId);
  const values = await formAction({
    title: "用原文补充关系",
    description: "同一条关系保存方向、两端称谓和逐字证据，不会创建第二套人物",
    submitLabel: "核验并保存关系",
    fields: [
      { name: "target_entity_id", label: "关系对象", type: "select", required: true, options: people.map((item) => ({ value: item.id, label: item.name })) },
      { name: "predicate", label: "正向称谓", placeholder: "例如：父亲、效忠、追捕或盟友", required: true },
      { name: "directionality", label: "关系方向", type: "select", value: "directed", options: [{ value: "directed", label: "单向关系" }, { value: "bidirectional", label: "双向关系" }] },
      { name: "reverse_predicate", label: "反向称谓", placeholder: "双向关系必填，例如：子女或徒弟" },
      { name: "segment_id", label: "原文片段", type: "select", required: true, options: state.overview.segments.map((segment) => ({ value: segment.id, label: segment.chapter_title })) },
      { name: "evidence_quote", label: "逐字原文", type: "textarea", rows: 4, required: true },
      { name: "summary", label: "关系说明", type: "textarea", rows: 3, required: true },
    ],
  });
  if (!values) return;
  const directionality = String(values.directionality);
  const reversePredicate = String(values.reverse_predicate || "").trim();
  if (directionality === "bidirectional" && !reversePredicate) {
    toast("双向关系需要填写反向称谓", true);
    return;
  }
  try {
    await api(`/api/connectivity-reviews/${reviewId}/relation`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_entity_id: Number(values.target_entity_id),
        predicate: String(values.predicate).trim(),
        directionality,
        reverse_predicate: reversePredicate || null,
        summary: String(values.summary).trim(),
        segment_id: Number(values.segment_id),
        evidence_quote: String(values.evidence_quote).trim(),
      }),
    });
    toast("关系已经建立，并通过逐字原文校验");
    await loadOverview(Number($("#progress-slider").value), true);
  } catch (error) {
    toast(error.message, true);
  }
}

async function resolveManualEventLocation(eventId) {
  const places = state.overview.entities.filter((item) => item.kind === "place");
  const values = await formAction({
    title: "用原文确认剧情地点",
    description: "地点只在逐字引文能够直接证明时写入正式地图",
    submitLabel: "核验并保存地点",
    fields: [
      { name: "location_entity_id", label: "发生地点", type: "select", required: true, options: places.map((item) => ({ value: item.id, label: item.name })) },
      { name: "segment_id", label: "原文片段", type: "select", required: true, options: state.overview.segments.map((segment) => ({ value: segment.id, label: segment.chapter_title })) },
      { name: "evidence_quote", label: "逐字原文", type: "textarea", rows: 4, required: true },
    ],
  });
  if (!values) return;
  try {
    await api(`/api/event-location-reviews/${eventId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ location_entity_id: Number(values.location_entity_id), segment_id: Number(values.segment_id), evidence_quote: String(values.evidence_quote).trim() }),
    });
    toast("剧情地点已经确认，并通过逐字原文校验");
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
  // 每次打开都登记唯一目标；较慢的旧证据请求返回时不得覆盖用户后来选择的新行程步；
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
    const summary = type === "event" ? eventNarrativeText(item) : item.summary || "当前条目没有补充说明";
    const aliases = item.aliases?.length ? item.aliases.join("、") : "—";
    const details = type === "entity"
      ? [["类别", categoryLabels[item.kind] || item.kind], ["别名", aliases], ["首次出现", chapterForSegment(item.first_segment)]]
      : type === "claim"
        ? [["关系", relationDisplay(item)], ["方向", item.directionality === "bidirectional" ? "双向关系" : "单向关系"], ["首次确认", chapterForSegment(item.first_segment)], ["审核状态", item.status], ["置信度", `${Math.round(item.confidence * 100)}%`]]
        : type === "event"
          ? [["故事时间", item.temporal_value || "未知"], ["原文章节", chapterForSegment(item.first_segment)], ["叙事层级", item.narrative_phase || "unknown"], ["地点", item.location_name || "未说明"], ["交通", transportLabels[item.transport] || "未说明"]]
          : type === "place_relation"
            ? [["方位关系", `${item.source_name} —${item.relative_position}→ ${item.target_name}`], ["首次确认", chapterForSegment(item.first_segment)], ["置信度", `${Math.round(item.confidence * 100)}%`]]
            : [["类别", categoryLabels[item.category] || item.category], ["首次出现", chapterForSegment(item.first_segment)], ["置信度", `${Math.round(item.confidence * 100)}%`]];
    const detailRows = details.map(([key, value]) => `<div class="detail-row"><span>${escapeHtml(key)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
    const evidenceCards = evidence.length ? evidence.map((source) => `<button class="evidence-card source-button" data-segment="${source.segment_id}" data-quote="${escapeHtml(source.quote)}" type="button"><blockquote>“${escapeHtml(source.quote)}”</blockquote><small>${escapeHtml(source.chapter_title)} · 点击回到原文</small></button>`).join("") : '<p class="detail-summary">当前记录缺少原文证据；它不会计入证据覆盖率</p>';
    const lineageItems = evidence.map((source) => source.lineage).filter((value) => value?.manifest || value?.model_call);
    const lineageKeys = new Set();
    const lineage = lineageItems.filter((value) => {
      const key = `${value.manifest?.id || "legacy"}:${value.model_call?.id || "local"}`;
      if (lineageKeys.has(key)) return false;
      lineageKeys.add(key);
      return true;
    });
    const lineagePanel = lineage.length ? `<details class="lineage-panel"><summary>查看这条结果的模型、提示词、成本和版本</summary>${lineage.map((value) => { const manifest = value.manifest; const call = value.model_call; return `<article><strong>${escapeHtml(call?.provider || manifest?.provider || "本地规则")} · ${escapeHtml(call?.model || manifest?.model || "未记录")}</strong><span>运行 #${manifest?.id || "旧记录"} · 调用 #${call?.id || "本地整理"} · ${escapeHtml(call?.status || manifest?.status || value.trace_status)}</span><span>提示词 ${escapeHtml((call?.prompt_hash || manifest?.prompt_hash || "旧版未记录").slice(0, 16))} · 合同 ${escapeHtml(manifest?.contract_version || "旧版未记录")}</span><span>输入 ${Number(call?.input_tokens || 0).toLocaleString()} · 输出 ${Number(call?.output_tokens || 0).toLocaleString()} · ${call?.estimated_cost_usd == null ? "订阅、本地或旧记录不换算美元" : escapeHtml(formatCost(call))}</span></article>`; }).join("")}</details>` : '<p class="lineage-legacy">这是升级前生成或完全由本地规则整理的记录；原文证据仍然有效，但旧版本没有完整运行清单</p>';
    const review = type === "claim" ? `<div class="review-actions"><button class="button button-quiet review-button" data-status="accepted" type="button">确认关系</button><button class="button button-danger review-button" data-status="rejected" type="button">标记错误</button></div>` : "";
    const relationEditor = type === "claim" ? `<details class="relation-editor"><summary>修改关系方向</summary><div class="form-stack"><label for="relation-direction">箭头方向</label><select id="relation-direction"><option value="directed" ${item.directionality !== "bidirectional" ? "selected" : ""}>单向</option><option value="bidirectional" ${item.directionality === "bidirectional" ? "selected" : ""}>双向</option></select><label for="relation-reverse-predicate">反向称谓</label><input id="relation-reverse-predicate" maxlength="80" value="${escapeHtml(item.reverse_predicate || "")}" placeholder="例如 子女、徒弟、配偶"><button class="button button-primary save-relation-direction" type="button">保存关系方向</button></div></details>` : "";
    const canRegenerate = ["world_note", "entry"].includes(type);
    const edit = `<div class="record-actions"><button class="button button-quiet edit-record" type="button">编辑全部属性</button>${canRegenerate ? '<button class="button button-quiet regenerate-record" type="button">按要求二次生成</button>' : ""}</div><div id="record-editor" class="record-editor semantic-record-editor" hidden>${recordEditFields(type, item, summary)}<button class="button button-primary save-record-edit" type="button">保存全部修改</button></div>${canRegenerate ? `<div id="draft-editor" class="draft-editor" hidden><label for="draft-instruction">使用陈述句写明整理任务</label><textarea id="draft-instruction" placeholder="补充证据中已经明确的适用条件、限制和后果；删除重复表述"></textarea><label for="draft-budget">本次草稿金额上限，美元</label><input id="draft-budget" type="number" min="0" step="0.01" value="0.05"><button class="button button-primary create-record-draft" type="button">生成候选版本</button><div id="draft-preview"></div></div>` : ""}`;
    const mapAction = type === "event" && item.location_entity_id ? `<button class="button button-quiet full map-jump" data-location="${item.location_entity_id}" type="button">在地图中查看地点</button>` : "";
    // 地图中的“下一步”必须沿主线行程前进；其他页面仍按完整编年事件前进；
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
    $("#inspector-body").innerHTML = `<p class="detail-summary">${escapeHtml(summary)}</p><div class="detail-list">${detailRows}</div>${relationEditor}${edit}${mapAction}${nextAction}${placeHistory}<h3 class="evidence-title">逐字原文证据</h3>${evidenceCards}<h3 class="evidence-title">生成溯源</h3>${lineagePanel}${review}`;
    $$(".source-button").forEach((button) => button.addEventListener("click", () => openSource(Number(button.dataset.segment), button.dataset.quote)));
    $$(".review-button").forEach((button) => button.addEventListener("click", () => reviewClaim(id, button.dataset.status)));
    $(".save-relation-direction")?.addEventListener("click", () => saveRelationDirection(item));
    $(".edit-record")?.addEventListener("click", () => { $("#record-editor").hidden = !$("#record-editor").hidden; });
    $(".save-record-edit")?.addEventListener("click", () => saveRecordEdits(type, id, item));
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

function recordEditFields(type, item, summary) {
  const entities = state.overview.entities || [];
  const entityOptions = (selected, kinds = null, allowUnknown = false) => `${allowUnknown ? `<option value="null" ${selected == null ? "selected" : ""}>未知或未说明</option>` : ""}${entities.filter((entity) => !kinds || kinds.includes(entity.kind)).map((entity) => `<option value="${entity.id}" ${Number(selected) === Number(entity.id) ? "selected" : ""}>${escapeHtml(entity.name)}</option>`).join("")}`;
  const text = (field, label, value, kind = "input") => `<label>${label}${kind === "textarea" ? `<textarea data-record-field="${field}">${escapeHtml(value || "")}</textarea>` : `<input data-record-field="${field}" value="${escapeHtml(value ?? "")}">`}</label>`;
  if (type === "entity") return `${text("name", "名称", item.name)}${text("aliases", "别名；使用逗号分隔", (item.aliases || []).join("，"))}<label>类别<select data-record-field="kind">${["person", "place", "faction", "creature", "other"].map((value) => `<option value="${value}" ${item.kind === value ? "selected" : ""}>${categoryLabels[value]}</option>`).join("")}</select></label><label>重要程度<input data-record-field="importance" type="number" min="0" max="1" step="0.05" value="${Number(item.importance || 0.5)}"></label>${text("summary", "说明", summary, "textarea")}`;
  if (type === "event") {
    const participants = (item.participants || []).map((person) => `${person.name}｜${person.role}`).join("\n");
    return `${text("title", "事件标题", item.title)}${text("summary", "事件说明", summary, "textarea")}${text("temporal_value", "故事时间", item.temporal_value)}<label>地点<select data-record-field="location_entity_id">${entityOptions(item.location_entity_id, ["place"], true)}</select></label><label>交通方式<select data-record-field="transport_mode">${Object.entries(transportLabels).map(([value, label]) => `<option value="${value}" ${item.transport === value ? "selected" : ""}>${label}</option>`).join("")}</select></label><label>参与者；每行使用“人物名｜角色”<textarea data-record-field="participants" data-participant-lines="true">${escapeHtml(participants)}</textarea></label>`;
  }
  if (type === "claim") return `<label>关系发出方<select data-record-field="source_entity_id">${entityOptions(item.source_entity_id)}</select></label><label>关系接收方<select data-record-field="target_entity_id">${entityOptions(item.target_entity_id)}</select></label>${text("predicate", "正向称谓", item.predicate)}<label>方向<select data-record-field="directionality"><option value="directed" ${item.directionality !== "bidirectional" ? "selected" : ""}>单向</option><option value="bidirectional" ${item.directionality === "bidirectional" ? "selected" : ""}>双向</option></select></label>${text("reverse_predicate", "反向称谓", item.reverse_predicate || "")}${text("temporal_scope", "有效时间", item.temporal_scope || "current")}${text("summary", "关系说明", summary, "textarea")}`;
  if (type === "place_relation") return `<label>起点<select data-record-field="source_entity_id">${entityOptions(item.source_entity_id, ["place"])}</select></label><label>终点<select data-record-field="target_entity_id">${entityOptions(item.target_entity_id, ["place"])}</select></label>${text("relative_position", "方位、包含或交通关系", item.relative_position)}${text("summary", "说明", summary, "textarea")}`;
  if (type === "world_note") return `${text("title", "标题", item.title)}${text("category", "分类", item.category)}${text("summary", "说明", summary, "textarea")}`;
  if (type === "entry") return `${text("name", "名称", item.name)}${text("category", "分类", item.category)}${text("summary", "说明", summary, "textarea")}${text("attributes", "结构化属性", JSON.stringify(item.attributes || {}, null, 2), "textarea")}`;
  return text("summary", "说明", summary, "textarea");
}

async function saveRecordEdits(type, id, original) {
  const fields = [...document.querySelectorAll("#record-editor [data-record-field]")];
  const entityByName = new Map((state.overview.entities || []).map((entity) => [entity.name, entity]));
  try {
    for (const field of fields) {
      let value = field.value.trim();
      if (field.dataset.participantLines === "true") {
        const participants = value.split(/\r?\n/).filter(Boolean).map((line) => {
          const [name, role] = line.split(/[|｜]/).map((part) => part.trim());
          const entity = entityByName.get(name);
          if (!entity || !role) throw new Error(`无法识别参与者“${line}”；请使用“人物名｜角色”`);
          return { entity_id: entity.id, role };
        });
        value = JSON.stringify(participants);
      }
      if (field.dataset.recordField === "aliases") value = JSON.stringify(value.split(/[，,]/).map((item) => item.trim()).filter(Boolean));
      await api(`/api/records/${type}/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ field_name: field.dataset.recordField, new_value: value || (field.dataset.recordField === "reverse_predicate" ? " " : "null"), reason: "用户在详情页修正" }) });
    }
    closeInspector();
    await loadOverview(Number($("#progress-slider").value));
    toast("全部修改已经保存；旧版本和原文证据仍然保留");
  } catch (error) { toast(error.message, true); }
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
    toast("修正已经保存，旧内容保留在修改记录中");
  } catch (error) {
    toast(error.message, true);
  }
}

async function generateRecordDraft(type, id) {
  const instruction = $("#draft-instruction")?.value.trim();
  const preview = $("#draft-preview");
  if (!instruction || instruction.length < 6) {
    preview.innerHTML = '<p class="detail-summary">整理任务至少写六个字，并明确要修改什么</p>';
    return;
  }
  if (/[?？]/.test(instruction)) {
    preview.innerHTML = '<p class="detail-summary">请把问句改成陈述式任务，例如“补充这条设定的限制和后果”</p>';
    return;
  }
  if (!/(补充|改写|整理|说明|突出|合并|修正|扩写|精简|生成)/.test(instruction)) {
    preview.innerHTML = '<p class="detail-summary">请写明补充、改写、整理或修正等具体任务</p>';
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
        toast("候选版本已经应用，修改前后的内容都已保存")
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
    toast(status === "accepted" ? "关系已确认，审核记录已保存" : "关系已标记错误，派生视图将隐藏它");
    closeInspector();
    await loadOverview(Number($("#progress-slider").value));
  } catch (error) {
    toast(error.message, true);
  }
}

async function saveRelationDirection(item) {
  const directionality = $("#relation-direction")?.value || "directed";
  const reversePredicate = $("#relation-reverse-predicate")?.value.trim() || null;
  if (directionality === "bidirectional" && !reversePredicate) {
    toast("双向关系需要填写反向称谓", true);
    return;
  }
  try {
    await api(`/api/claims/${item.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        status: item.status,
        directionality,
        reverse_predicate: reversePredicate,
        reason: "用户在关系详情中修正方向",
      }),
    });
    closeInspector();
    await loadOverview(Number($("#progress-slider").value));
    toast("关系方向已经保存，并记录了修改历史");
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
    openDialog("#source-dialog", "#source-close");
  } catch (error) {
    toast(error.message, true);
  }
}

function closeInspector() {
  // 关闭后解除与行程的联动，并让尚未返回的证据请求失效；
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
  const panel = $("#view-panel");
  if (!panel) return;
  const children = new Map();
  state.folders.forEach((folder) => {
    const key = folder.parent_id === null ? "root" : String(folder.parent_id);
    if (!children.has(key)) children.set(key, []);
    children.get(key).push(folder);
  });
  const folderTree = (parent = "root", depth = 0, trail = new Set()) => (children.get(String(parent)) || []).map((folder) => {
    if (trail.has(Number(folder.id))) return "";
    const nextTrail = new Set(trail).add(Number(folder.id));
    const count = state.books.filter((book) => Number(book.folder_id) === Number(folder.id)).length;
    const editing = Number(state.libraryEditingFolderId) === Number(folder.id);
    return `<div class="library-folder-node" style="--folder-depth:${depth}" data-folder="${folder.id}"><div class="library-folder-line"><button class="library-folder-select${String(state.libraryFolderId) === String(folder.id) ? " active" : ""}" type="button"><span>▸</span><strong>${escapeHtml(folder.name)}</strong><small>${count}</small></button><button class="library-inline-action edit-folder" type="button" aria-label="编辑 ${escapeHtml(folder.name)}">编辑</button><button class="library-inline-action delete-folder" type="button" aria-label="删除 ${escapeHtml(folder.name)}">删除</button></div>${editing ? `<div class="library-folder-editor"><input class="folder-name-edit" value="${escapeHtml(folder.name)}" aria-label="文件夹名称"><select class="folder-parent-edit" aria-label="上级目录">${folderOptions(folder.parent_id, folder.id)}</select><div><button class="button button-primary save-folder" type="button">保存</button><button class="button button-quiet cancel-folder-edit" type="button">取消</button></div></div>` : ""}${folderTree(folder.id, depth + 1, nextTrail)}</div>`;
  }).join("");
  const query = state.libraryQuery.trim().toLocaleLowerCase();
  const filteredBooks = state.books.filter((book) => {
    const folderMatch = state.libraryFolderId === "all"
      || (state.libraryFolderId === "root" ? book.folder_id === null : Number(book.folder_id) === Number(state.libraryFolderId));
    const queryMatch = !query || `${book.title} ${book.author || ""}`.toLocaleLowerCase().includes(query);
    return folderMatch && queryMatch;
  });
  if (!filteredBooks.some((book) => Number(book.id) === Number(state.libraryBookId))) {
    state.libraryBookId = filteredBooks[0]?.id || null;
  }
  const bookCards = filteredBooks.map((book) => `<button class="library-book-card${Number(book.id) === Number(state.libraryBookId) ? " active" : ""}" data-book="${book.id}" type="button"><span class="library-book-mark" aria-hidden="true">书</span><span><strong>${escapeHtml(book.title)}</strong><small>${escapeHtml(book.author || "作者未填写")} · ${Number(book.segment_count || 0)} 个原文片段 · ${Number(book.character_count || 0).toLocaleString()} 字</small><small>${book.corpus_kind === "open_real" ? "真实开放作品" : book.corpus_kind === "synthetic" ? "系统虚构" : "用户导入"}${book.language ? ` · ${escapeHtml(book.language)}` : ""} · 已分析 ${Number(book.analyzed_segment_count || 0)}/${Number(book.segment_count || 0)}</small></span></button>`).join("");
  const selectedBook = state.books.find((book) => Number(book.id) === Number(state.libraryBookId));
  const selectedFolder = selectedBook?.folder_id === null ? "根目录" : state.folders.find((folder) => Number(folder.id) === Number(selectedBook?.folder_id))?.name || "待归类";
  const editingBook = selectedBook && Number(state.libraryEditingBookId) === Number(selectedBook.id);
  const bookDetail = selectedBook ? `<div class="library-book-detail" data-book="${selectedBook.id}"><span class="eyebrow">书籍详情</span><h3>${escapeHtml(selectedBook.title)}</h3>${editingBook ? `<div class="library-book-editor form-stack"><label for="library-book-title">书名</label><input id="library-book-title" class="book-title-edit" value="${escapeHtml(selectedBook.title)}"><label for="library-book-author">作者</label><input id="library-book-author" class="book-author-edit" value="${escapeHtml(selectedBook.author || "")}"><label for="library-book-folder">所在文件夹</label><select id="library-book-folder" class="book-folder-edit">${folderOptions(selectedBook.folder_id)}</select><label for="library-book-report-language">生成语言</label><select id="library-book-report-language" class="book-report-language-edit">${Object.entries(reportLanguageLabels).map(([value, label]) => `<option value="${value}" ${value === (selectedBook.report_language || "follow_source") ? "selected" : ""}>${escapeHtml(label)}</option>`).join("")}</select><small class="field-help">控制后续摘要、说明和报告；原文与逐字证据永远不改写；已有结果不会自动重跑</small><div class="action-bar"><button class="button button-primary save-book" type="button">保存修改</button><button class="button button-quiet cancel-book-edit" type="button">取消</button></div></div>` : `<dl><div><dt>作者</dt><dd>${escapeHtml(selectedBook.author || "未填写")}</dd></div><div><dt>分类</dt><dd>${escapeHtml(selectedFolder)}</dd></div><div><dt>作品类型</dt><dd>${selectedBook.corpus_kind === "open_real" ? "真实开放作品" : selectedBook.corpus_kind === "synthetic" ? "功能演示 · 系统虚构" : "用户导入"}</dd></div><div><dt>原文语言</dt><dd>${escapeHtml(selectedBook.language || "未记录")}</dd></div><div><dt>生成语言</dt><dd>${escapeHtml(reportLanguageLabels[selectedBook.report_language || "follow_source"] || "跟随原文")}</dd></div><div><dt>许可</dt><dd>${escapeHtml(selectedBook.license_name || "用户自行确认使用权")}</dd></div><div><dt>分析范围</dt><dd>${Number(selectedBook.analyzed_segment_count || 0)}/${Number(selectedBook.segment_count || 0)} 个片段</dd></div><div><dt>原文片段</dt><dd>${Number(selectedBook.segment_count || 0)}</dd></div><div><dt>全文字符</dt><dd>${Number(selectedBook.character_count || 0).toLocaleString()}</dd></div><div><dt>导入时间</dt><dd>${escapeHtml(selectedBook.created_at || "未记录")}</dd></div><div><dt>更新时间</dt><dd>${escapeHtml(selectedBook.updated_at || "未记录")}</dd></div></dl>${selectedBook.source_url ? `<a class="button button-quiet full" href="${escapeHtml(selectedBook.source_url)}" target="_blank" rel="noreferrer">查看作品来源</a>` : ""}<div class="library-detail-actions"><button class="button button-primary open-book" type="button">打开这本书</button><button class="button button-quiet edit-book" type="button">编辑资料</button><button class="button button-danger delete-book-row" type="button">删除书籍</button></div>`}</div>` : emptyState("没有符合条件的书籍", "更换文件夹或清除搜索条件后再查看");
  const folderTreeHtml = folderTree();
  panel.innerHTML = `${panelHead("本机书库", "整理文件夹、书籍资料和分析入口，离开后会恢复原来的阅读视图", `<button id="library-back" class="button button-quiet" type="button">返回阅读</button>`)}<div class="library-workspace"><aside class="library-folder-pane"><div class="library-pane-head"><strong>文件夹</strong><button id="show-folder-create" class="library-inline-action" type="button">新建</button></div><button class="library-folder-select${state.libraryFolderId === "all" ? " active" : ""}" data-folder="all" type="button"><span>◇</span><strong>全部书籍</strong><small>${state.books.length}</small></button><button class="library-folder-select${state.libraryFolderId === "root" ? " active" : ""}" data-folder="root" type="button"><span>⌂</span><strong>根目录</strong><small>${state.books.filter((book) => book.folder_id === null).length}</small></button>${folderTreeHtml || '<p class="library-empty-copy">还没有文件夹</p>'}<div id="library-folder-create" class="library-folder-create form-stack" hidden><label for="new-folder-name">文件夹名称</label><input id="new-folder-name" maxlength="120"><label for="new-folder-parent">上级目录</label><select id="new-folder-parent">${folderOptions()}</select><button id="create-folder-button" class="button button-primary" type="button">创建文件夹</button></div></aside><section class="library-books-pane"><div class="library-searchbar"><input id="library-search" type="search" value="${escapeHtml(state.libraryQuery)}" placeholder="搜索书名或作者"><span>${filteredBooks.length} 本</span></div><div class="library-book-list">${bookCards || emptyState("没有符合条件的书籍", "可以调整文件夹或搜索条件")}</div></section><aside class="library-detail-pane">${bookDetail}</aside></div>`;
  $("#library-back")?.addEventListener("click", () => {
    state.view = state.viewBeforeLibrary || "relationships";
    renderView();
  });
  $("#show-folder-create")?.addEventListener("click", () => { $("#library-folder-create").hidden = !$("#library-folder-create").hidden; });
  $("#create-folder-button")?.addEventListener("click", createFolder);
  $("#library-search")?.addEventListener("input", (event) => {
    state.libraryQuery = event.target.value;
    renderLibraryManager();
    $("#library-search")?.focus();
  });
  $$(".library-folder-select").forEach((button) => button.addEventListener("click", () => {
    state.libraryFolderId = button.dataset.folder || button.closest("[data-folder]")?.dataset.folder || "all";
    state.libraryBookId = null;
    renderLibraryManager();
  }));
  $$(".library-book-card").forEach((button) => button.addEventListener("click", () => { state.libraryBookId = Number(button.dataset.book); state.libraryEditingBookId = null; renderLibraryManager(); }));
  $$(".edit-folder").forEach((button) => button.addEventListener("click", () => { state.libraryEditingFolderId = Number(button.closest("[data-folder]").dataset.folder); renderLibraryManager(); }));
  $$(".cancel-folder-edit").forEach((button) => button.addEventListener("click", () => { state.libraryEditingFolderId = null; renderLibraryManager(); }));
  $$(".save-folder").forEach((button) => button.addEventListener("click", () => saveFolder(button.closest("[data-folder]"))));
  $$(".delete-folder").forEach((button) => button.addEventListener("click", () => deleteFolder(button.closest("[data-folder]"))));
  $(".open-book")?.addEventListener("click", () => openManagedBook($(".library-book-detail")));
  $(".edit-book")?.addEventListener("click", () => { state.libraryEditingBookId = Number(state.libraryBookId); renderLibraryManager(); });
  $(".cancel-book-edit")?.addEventListener("click", () => { state.libraryEditingBookId = null; renderLibraryManager(); });
  $(".save-book")?.addEventListener("click", () => saveManagedBook($(".library-book-detail")));
  $(".delete-book-row")?.addEventListener("click", () => deleteManagedBook($(".library-book-detail")));
}

async function openLibraryManager() {
  try {
    await refreshLibraryData();
    if (state.view !== "library") state.viewBeforeLibrary = state.view;
    state.view = "library";
    state.libraryBookId = state.bookId;
    renderView();
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
    state.libraryEditingFolderId = null;
    renderLibraryManager();
    toast("文件夹已经保存");
  } catch (error) { toast(error.message, true); }
}

async function deleteFolder(row) {
  const id = Number(row.dataset.folder);
  if (!await confirmAction("删除文件夹", "其中的书籍和子文件夹会移回根目录，不会删除书籍内容", "删除文件夹")) return;
  try {
    await api(`/api/library/folders/${id}`, { method: "DELETE" });
    await refreshLibraryData();
    renderLibraryManager();
  } catch (error) { toast(error.message, true); }
}

async function openManagedBook(row) {
  persistMapCameraState();
  state.bookId = Number(row.dataset.book);
  resetMapStateForBook();
  $("#book-select").value = String(state.bookId);
  state.view = state.viewBeforeLibrary || "relationships";
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
        report_language: row.querySelector(".book-report-language-edit").value,
      }),
    });
    await refreshLibraryData();
    state.libraryEditingBookId = null;
    renderLibraryManager();
    if (Number(state.bookId) === id) await loadOverview(null, true);
    toast("书籍信息和生成语言已经保存；原文与已有事实保持不变");
  } catch (error) { toast(error.message, true); }
}

async function deleteManagedBook(row) {
  const id = Number(row.dataset.book);
  const book = state.books.find((item) => Number(item.id) === id);
  if (!await confirmAction("删除书籍", `《${book?.title || "这本书"}》的原文、分析结果和修改记录将被删除`, "删除书籍")) return;
  try {
    await api(`/api/books/${id}`, { method: "DELETE" });
    if (Number(state.bookId) === id) {
      persistMapCameraState();
      state.bookId = null;
    }
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
    persistMapCameraState();
    state.bookId = Number(result.id);
    state.mapStep = 0;
    state.mapViewport = null;
    $("#book-select").value = String(state.bookId);
    await loadOverview();
    toast(`已导入《${result.title}》，共 ${result.segments} 个证据片段`);
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
  openDialog("#update-dialog", "#book-update-file");
  await loadUpdateHistory();
}

function renderUpdateResult(result) {
  const conflicts = result.conflicts || [];
  if (result.status === "needs_review") {
    const cards = conflicts.map((item) => `<article class="update-conflict"><strong>第 ${Number(item.ordinal) + 1} 个片段 · ${escapeHtml(item.kind)}</strong><small>${escapeHtml(item.old_title)} → ${escapeHtml(item.new_title)}</small><p>当前版本：${escapeHtml(item.old_excerpt || "无")}</p><p>上传版本：${escapeHtml(item.new_excerpt || "无")}</p></article>`).join("");
    const reused = Math.round(Number(result.reuse_ratio || 0) * 100);
    const scope = result.affected_scope || {};
    $("#update-result").innerHTML = `<div class="update-summary warning"><strong>可复用既有分析 ${reused}%</strong><span>需要重跑 ${Number((scope.context_ordinals || []).length)} 个片段；旧版本、人工编辑和未变化章节都会保留</span></div><div class="update-conflicts">${cards}</div><div class="update-actions"><button class="button button-quiet resolve-update" data-id="${result.id}" data-action="keep_current" type="button">暂不更新</button><button class="button button-quiet resolve-update" data-id="${result.id}" data-action="import_as_new" type="button">另存为新书</button><button class="button button-primary resolve-update" data-id="${result.id}" data-action="apply_incremental" type="button">应用增量更新</button></div>`;
  } else {
    const added = Number(result.added_segment_count || 0);
    $("#update-result").innerHTML = `<div class="update-summary">${escapeHtml(result.message)}${added ? ` 新增 ${added} 个片段，分析起点为第 ${Number(result.start_segment) + 1} 个片段` : ""}</div>${added ? `<button class="button button-primary analyze-added" data-start="${result.start_segment}" type="button">只分析新增内容</button>` : ""}`;
  }
  $$(".resolve-update").forEach((button) => button.addEventListener("click", () => resolveUpdate(Number(button.dataset.id), button.dataset.action)));
  $(".analyze-added")?.addEventListener("click", (event) => {
    closeDialog("#update-dialog");
    analyze(Number(event.currentTarget.dataset.start));
  });
}

async function submitBookUpdate() {
  const file = $("#book-update-file").files[0];
  if (!file) {
    toast("请先选择更新文件", true);
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
    if (action === "apply_incremental") {
      persistMapCameraState();
      state.bookId = Number(result.book_id);
      $("#book-select").value = String(state.bookId);
      resetMapStateForBook();
      await loadOverview();
      closeDialog("#update-dialog");
      toast(`增量更新已应用；复用 ${Math.round(Number(result.reuse_ratio || 0) * 100)}% 既有分析`);
    } else if (action !== "keep_current") {
      persistMapCameraState();
      state.bookId = Number(result.book_id);
      $("#book-select").value = String(state.bookId);
      state.mapStep = 0;
      state.mapViewport = null;
      await loadOverview();
      closeDialog("#update-dialog");
      toast("上传版本已经另存；当前版本完整保留");
    } else {
      $("#update-result").innerHTML = '<div class="update-summary">本次更新已结束，当前版本和既有分析保持不变</div>';
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
  openDialog("#analysis-dialog", "#analysis-budget");
  await refreshAnalysisEstimate();
}

async function refreshAnalysisEstimate() {
  const provider = $("#provider-select").value;
  const reviewMode = $("#analysis-review-mode").value;
  const estimateBox = $("#analysis-estimate");
  estimateBox.textContent = "正在渲染实际请求并结合历史账本预测…";
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
  const amount = estimate.conservative_cost_usd ?? estimate.estimated_cost_usd;
  const medianAmount = estimate.median_cost_usd;
  const over = amount !== null && Number(amount) > budget;
  const estimateBox = $("#analysis-estimate");
  const amountLabel = amount === null ? "当前价格不可复算" : `$${Number(amount).toFixed(Number(amount) < 0.01 ? 6 : 4)}`;
  const medianLabel = medianAmount === null || medianAmount === undefined ? "样本不足" : `$${Number(medianAmount).toFixed(Number(medianAmount) < 0.01 ? 6 : 4)}`;
  const confidenceLabel = ({ high: "高", medium: "中", low: "低" })[estimate.confidence] || "待校准";
  estimateBox.classList.toggle("over", false);
  const backtest = estimate.backtest;
  estimateBox.innerHTML = `<div class="forecast-grid"><span>预计中位值<strong>${escapeHtml(medianLabel)}</strong></span><span>保守上限<strong>${escapeHtml(amountLabel)}</strong></span><span>待调用片段<strong>${Number(estimate.pending_segments ?? estimate.segment_count).toLocaleString()}</strong></span><span>本地缓存<strong>${Number(estimate.exact_cache_segments || 0).toLocaleString()}</strong></span></div><p>置信度 ${escapeHtml(confidenceLabel)} · ${Number(estimate.sample_count || 0)} 条真实调用样本 · 输入中位约 ${Number(estimate.median_input_tokens || 0).toLocaleString()} · 输出中位约 ${Number(estimate.median_output_tokens || 0).toLocaleString()} 令牌${backtest ? ` · 10/${Number(backtest.validation_segments)} 回放误差 ${escapeHtml(backtest.absolute_error_percent ?? "无法计价")}%` : ""}</p><small>${over ? `初始参考为 $${budget.toFixed(2)}，硬预算不会被预测值悄悄改写，自动扩展会留下原因和记录` : "前三个未缓存片段完成后会用真实密度校准剩余预测，保守上限用于防止失控调用"}</small>`;
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
      closeDialog("#analysis-dialog");
      renderJob(updated);
      toast("自动适配范围已保存，点击继续即可恢复任务")
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
    closeDialog("#analysis-dialog");
    state.activeJobId = job.id;
    renderJob(job);
    if (job.status === "completed" && Number(job.total_segments) === 0) {
      toast("整本书已经分析完成，没有重复调用模型");
    } else {
      toast("整本书分析已经开始，可以关闭页面或稍后继续查看");
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
  openDialog("#search-dialog", "#search-close");
  try {
    const visible = Number($("#progress-slider").value);
    const results = await api(`/api/books/${state.bookId}/search?q=${encodeURIComponent(query)}&through_segment=${visible}`);
    $("#search-results").innerHTML = results.length ? results.map((result) => `<button class="search-result" data-type="${escapeHtml(result.target_type)}" data-id="${result.target_id}" type="button"><strong>${escapeHtml(result.title)}</strong><span>${escapeHtml(result.snippet)}</span></button>`).join("") : emptyState("没有找到匹配内容", "可以更换人物别名、地点或原文词句");
    $$(".search-result").forEach((button) => button.addEventListener("click", () => {
      closeDialog("#search-dialog");
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
  if (!await confirmAction("删除当前书籍", `《${title}》的原文、分析结果和修改记录将被删除，建议先备份`, "删除书籍")) return;
  try {
    await api(`/api/books/${state.bookId}`, { method: "DELETE" });
    persistMapCameraState();
    await refreshLibraryData();
    if (state.books.length) {
      state.bookId = Number(state.books[0].id);
      state.mapStep = 0;
      state.mapViewport = null;
      $("#book-select").value = String(state.bookId);
      await loadOverview();
    } else {
      state.bookId = null;
      $("#view-panel").innerHTML = emptyState("书库是空的", "导入一本小说后即可开始分析");
    }
    toast(`《${title}》已经从本机数据库删除`);
  } catch (error) {
    toast(error.message, true);
  }
}

async function saveProviderKey() {
  const provider = $("#key-provider").value;
  const apiKey = $("#provider-key").value.trim();
  if (!apiKey) {
    toast("请先粘贴开放平台密钥", true);
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
    closeDialog("#key-dialog");
    toast("模型密钥已由 Windows 当前账户加密保存");
  } catch (error) {
    toast(error.message, true);
  }
}

async function deleteProviderKey() {
  const provider = $("#key-provider").value;
  if (!await confirmAction("删除模型密钥", "这个平台保存在本机的密钥将被删除", "删除密钥")) return;
  try {
    await api(`/api/settings/provider-key/${provider}`, { method: "DELETE" });
    state.providers = await api("/api/providers");
    renderProviderOptions();
    closeDialog("#key-dialog");
    toast("本机保存的模型密钥已经删除");
  } catch (error) {
    toast(error.message, true);
  }
}

// 绑定全局操作，视图内部操作会在每次渲染后重新绑定；
document.addEventListener("change", (event) => {
  if (!event.target.matches("#story-scope-select")) return;
  const [kind, rawId] = event.target.value.split(":");
  persistMapCameraState();
  state.storyScope = { kind, id: Number(rawId) };
  state.mapStep = 0;
  state.mapViewport = null;
  renderView();
});
$("#book-select").addEventListener("change", async (event) => {
  clearTimeout(state.jobTimer);
  state.activeJobId = null;
  persistMapCameraState();
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
$("#update-book-button").addEventListener("click", openUpdateDialog);
$("#update-close").addEventListener("click", () => closeDialog("#update-dialog"));
$("#preview-update-button").addEventListener("click", submitBookUpdate);
$("#analyze-button").addEventListener("click", () => analyze(0));
$("#analysis-close").addEventListener("click", () => closeDialog("#analysis-dialog"));
$("#analysis-reestimate").addEventListener("click", refreshAnalysisEstimate);
$("#analysis-start").addEventListener("click", startAnalysis);
$("#analysis-budget").addEventListener("input", renderAnalysisEstimate);
$("#analysis-review-mode").addEventListener("change", refreshAnalysisEstimate);
$("#key-settings-button").addEventListener("click", () => openDialog("#key-dialog", "#key-provider"));
$("#key-close").addEventListener("click", () => closeDialog("#key-dialog"));
$("#key-save").addEventListener("click", saveProviderKey);
$("#key-delete").addEventListener("click", deleteProviderKey);
$("#global-search").addEventListener("keydown", (event) => { if (event.key === "Enter") searchBook(); });
$("#global-search-button").addEventListener("click", searchBook);
$("#export-button").addEventListener("click", () => download(`/api/books/${state.bookId}/export?include_text=true`));
$("#backup-button").addEventListener("click", () => download("/api/backup"));
$("#delete-button").addEventListener("click", deleteCurrentBook);
$("#progress-slider").addEventListener("input", (event) => {
  const value = Number(event.target.value);
  $("#progress-count").textContent = `第 ${value + 1} 章 · 共 ${state.overview?.segments?.length || 0} 章`;
  $("#progress-chapter").textContent = "松开后显示对应进度";
});
$("#progress-slider").addEventListener("change", (event) => loadOverview(Number(event.target.value)));
$("#progress-count").addEventListener("dblclick", beginProgressEdit);
$("#progress-count").addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    beginProgressEdit();
  }
});
$$(".nav-item").forEach((item) => item.addEventListener("click", () => { state.view = item.dataset.view; renderView(); }));
$("#inspector-close").addEventListener("click", closeInspector);
$("#scrim").addEventListener("click", closeInspector);
$("#source-close").addEventListener("click", () => closeDialog("#source-dialog"));
$("#search-close").addEventListener("click", () => closeDialog("#search-dialog"));
$("#confirm-close").addEventListener("click", () => finishConfirmation(false));
$("#confirm-cancel").addEventListener("click", () => finishConfirmation(false));
$("#confirm-accept").addEventListener("click", () => finishConfirmation(true));
$("#confirm-dialog").addEventListener("cancel", (event) => {
  event.preventDefault();
  finishConfirmation(false);
});
$("#form-dialog-close").addEventListener("click", () => finishFormAction(false));
$("#form-dialog-cancel").addEventListener("click", () => finishFormAction(false));
$("#form-dialog-submit").addEventListener("click", () => finishFormAction(true));
$("#form-dialog-fields").addEventListener("submit", (event) => {
  event.preventDefault();
  finishFormAction(true);
});
$("#form-dialog").addEventListener("cancel", (event) => {
  event.preventDefault();
  finishFormAction(false);
});
$$('dialog').forEach((dialog) => dialog.addEventListener("close", () => {
  const trigger = state.dialogReturnFocus.get(dialog.id);
  state.dialogReturnFocus.delete(dialog.id);
  if (trigger?.isConnected) trigger.focus();
}));
document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeInspector(); });
window.addEventListener("resize", () => {
  if (!$("#inspector").classList.contains("open")) return;
  $("#scrim").hidden = !window.matchMedia("(max-width: 1100px)").matches;
});

const selectObserver = new MutationObserver((records) => {
  if (state.selectEnhanceFrame) return;
  if (!records.some((record) => [...record.addedNodes].some((node) => node.nodeType === Node.ELEMENT_NODE))) return;
  state.selectEnhanceFrame = requestAnimationFrame(() => {
    state.selectEnhanceFrame = null;
    enhanceSelects();
  });
});
selectObserver.observe(document.body, { childList: true, subtree: true });
enhanceSelects();

initialize();
