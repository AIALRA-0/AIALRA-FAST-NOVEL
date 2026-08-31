const { test, expect } = require("playwright/test");

const baseURL = process.env.NOVEL_ATLAS_E2E_URL || "http://127.0.0.1:8766";

async function selectBook(page, label, { waitForLoad = true } = {}) {
  await page.locator("#book-select").evaluate((select, expected) => {
    const option = [...select.options].find((item) => item.textContent === expected);
    if (!option) throw new Error(`找不到书籍：${expected}`);
    select.value = option.value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }, label);
  if (waitForLoad) await expect(page.locator("#book-title")).toContainText(label, { timeout: 15000 });
}

async function chooseHiddenSelect(page, selector, value) {
  await page.locator(selector).evaluate((select, selected) => {
    select.value = selected;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }, value);
}

test("2D 与 3D 地图共享编年状态并支持快速连续切换", async ({ page }) => {
  const errors = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(baseURL, { waitUntil: "networkidle" });
  await expect(page.locator(".brand span")).toContainText("2.9.7");
  await page.locator('.nav-item[data-view="map"]').click();
  const totalSteps = Number(await page.locator("#map-step-slider").getAttribute("max")) + 1;
  const totalPlaces = await page.locator(".map-node").count();
  expect(totalSteps).toBeGreaterThan(1);
  expect(totalPlaces).toBeGreaterThan(0);
  await expect(page.locator("#map-step-count")).toHaveText(`第 1 步 · 共 ${totalSteps} 步`);
  await expect(page.locator(".map-svg")).toBeVisible();
  expect(await page.locator(".semantic-region").count()).toBeGreaterThanOrEqual(1);

  for (let index = 0; index < 5; index += 1) {
    await page.locator("#map-next").dispatchEvent("click");
  }
  await expect(page.locator("#map-step-count")).toHaveText(`第 6 步 · 共 ${totalSteps} 步`);
  const stepTitle = await page.locator("#map-event-card h3").textContent();

  await page.locator('.map-mode[data-mode="3d"]').click();
  await expect(page.locator("#map-step-count")).toHaveText(`第 6 步 · 共 ${totalSteps} 步`);
  await expect(page.locator("#map-event-card h3")).toHaveText(stepTitle);
  await expect(page.locator("#map-3d canvas")).toBeVisible();
  for (let index = 0; index < 8; index += 1) {
    await page.locator("#map-next").dispatchEvent("click");
  }
  await expect(page.locator("#map-step-count")).toHaveText(`第 14 步 · 共 ${totalSteps} 步`);

  await expect(page.locator(".map-3d-axis")).toContainText("平面方位未知");
  await page.screenshot({ path: "output/playwright/v2.9-map-3d.png", fullPage: true });
  await page.locator('.map-mode[data-mode="2d"]').click();
  await expect(page.locator("#map-step-count")).toHaveText(`第 14 步 · 共 ${totalSteps} 步`);
  await expect(page.locator(".map-svg")).toBeVisible();
  await page.locator("#map-step-slider").evaluate((slider) => {
    slider.value = slider.max;
    slider.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await expect(page.locator("#map-step-count")).toHaveText(`第 ${totalSteps} 步 · 共 ${totalSteps} 步`);
  await expect(page.locator("#map-next")).toBeDisabled();
  await expect(page.locator("#map-next")).toHaveText("已到末步");
  await page.waitForTimeout(260);
  const visibleRoutes = await page.locator(".journey-route").evaluateAll((routes) =>
    routes.filter((route) => Number.parseFloat(getComputedStyle(route).opacity) > 0.01).length,
  );
  const visiblePlaces = await page.locator(".map-node").evaluateAll((nodes) =>
    nodes.filter((node) => Number.parseFloat(getComputedStyle(node).opacity) > 0.01).length,
  );
  expect(visibleRoutes).toBeLessThanOrEqual(9);
  expect(visiblePlaces).toBe(totalPlaces);
  await page.screenshot({ path: "output/playwright/v2.8-map-2d.png", fullPage: true });

  expect(errors).toEqual([]);
});

test("黑白界面在窄屏仍可操作且键盘焦点可见", async ({ page }) => {
  const errors = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));

  await page.setViewportSize({ width: 720, height: 960 });
  await page.goto(baseURL, { waitUntil: "networkidle" });
  await page.locator('.nav-item[data-view="map"]').click();
  await expect(page.locator("#map-next")).toBeVisible();
  await expect(page.locator("#map-next")).toHaveCSS("min-height", "44px");
  await page.locator("#map-next").focus();
  const outlineWidth = await page.locator("#map-next").evaluate((element) => getComputedStyle(element).outlineWidth);
  expect(Number.parseFloat(outlineWidth)).toBeGreaterThanOrEqual(3);
  await page.locator('.map-mode[data-mode="3d"]').click();
  await expect(page.locator("#map-3d canvas")).toBeVisible();
  await page.screenshot({ path: "output/playwright/v2.9-mobile.png", fullPage: true });
  expect(errors).toEqual([]);
});

test("三层知识库可检索并在固定右栏显示证据、编辑和历史", async ({ page }) => {
  const errors = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(baseURL, { waitUntil: "networkidle" });
  await selectBook(page, "长夜十二城 · 120章大型压力演示");
  await page.locator('.nav-item[data-view="database"]').click();
  await expect(page.locator(".knowledge-workspace")).toBeVisible();
  await page.waitForTimeout(250);
  await page.locator("#entry-search").fill("核心危机");
  await expect(page.locator(".concept-row")).toHaveCount(1);
  await page.locator(".concept-row").filter({ hasText: "核心危机" }).first().click();
  await expect(page.locator("#inspector")).toHaveClass(/open/);
  await expect(page.locator(".knowledge-claim")).toHaveCount(1);
  await expect(page.locator(".knowledge-history")).toBeVisible();
  const titleBox = await page.locator("h1").boundingBox();
  expect(titleBox.width).toBeGreaterThan(180);
  expect(titleBox.height).toBeLessThan(180);
  await page.screenshot({ path: "output/playwright/v2.8-knowledge.png", fullPage: true });
  expect(errors).toEqual([]);
});

test("切换书籍遇到慢接口时知识详情不会串到上一本书", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(baseURL, { waitUntil: "networkidle" });
  await selectBook(page, "霓虹追凶 · 都市群像演示");
  await expect(page.locator("#book-title")).toContainText("霓虹追凶");
  await page.locator('.nav-item[data-view="database"]').click();
  await expect(page.locator(".knowledge-workspace")).toBeVisible();
  await page.route(/\/api\/books\/\d+\/concepts/, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 2500));
    await route.continue();
  });

  await selectBook(page, "长夜十二城 · 120章大型压力演示", { waitForLoad: false });
  await expect(page.locator(".concept-row")).toHaveCount(0, { timeout: 1000 });
  await expect(page.locator(".knowledge-workspace")).toBeVisible();
  await page.locator("#entry-search").fill("核心危机");
  const row = page.locator(".concept-row").filter({ hasText: "长夜十二城的核心危机" });
  await expect(row).toHaveCount(1);
  await row.click();
  await expect(page.locator("#inspector-title")).toHaveText("长夜十二城的核心危机");
  await expect(page.locator(".knowledge-claim")).toHaveCount(1);
});

test("真实长篇世界图保留全部节点并分级显示标签", async ({ page }) => {
  test.skip(!process.env.NOVEL_ATLAS_REAL_LONG, "需要本机真实长篇验收副本");
  const errors = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));

  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.goto(baseURL, { waitUntil: "networkidle" });
  const overviewReady = page.waitForResponse((response) => /\/api\/books\/\d+\/overview/.test(response.url()) && response.ok());
  await selectBook(page, "西游记");
  const overview = await (await overviewReady).json();
  const expectedSteps = overview.story_map_steps.length;
  const expectedPlaces = overview.entities.filter((entity) => entity.kind === "place").length;
  expect(expectedSteps).toBeGreaterThan(1_000);
  expect(expectedPlaces).toBeGreaterThan(80);
  await page.locator('.nav-item[data-view="map"]').click();
  await expect(page.locator("#map-step-count")).toHaveText(`1/${expectedSteps}`);
  const nodeCount = await page.locator(".map-node").count();
  const retainedLabels = await page.locator(".map-node-label").count();
  const collisionHiddenLabels = await page.locator(".map-node-label-collided").count();
  expect(nodeCount).toBe(expectedPlaces);
  expect(retainedLabels).toBe(nodeCount);
  expect(collisionHiddenLabels).toBeGreaterThan(0);
  const visibleLabelOverlaps = await page.locator(".map-node-label:not(.map-node-label-collided) .map-label-bg").evaluateAll((labels) => {
    const boxes = labels.map((label) => ({ box: label.getBoundingClientRect(), text: label.parentElement?.textContent.trim() || "" })).filter((item) =>
      item.box.width > 0 && item.box.height > 0 && item.box.right > 0 && item.box.bottom > 0
        && item.box.left < window.innerWidth && item.box.top < window.innerHeight
    );
    const overlaps = [];
    for (let left = 0; left < boxes.length; left += 1) {
      for (let right = left + 1; right < boxes.length; right += 1) {
        const a = boxes[left].box;
        const b = boxes[right].box;
        if (!(a.right < b.left || a.left > b.right || a.bottom < b.top || a.top > b.bottom)) {
          overlaps.push(`${boxes[left].text}/${boxes[right].text}`);
        }
      }
    }
    return overlaps;
  });
  await page.screenshot({ path: "output/playwright/v2.8-xiyouji-map.png", fullPage: false });
  expect(visibleLabelOverlaps).toEqual([]);

  const title = await page.locator("#map-event-card h3").textContent();
  await page.locator('.map-mode[data-mode="3d"]').click();
  await expect(page.locator("#map-step-count")).toHaveText(`1/${expectedSteps}`);
  await expect(page.locator("#map-event-card h3")).toHaveText(title);
  await expect(page.locator("#map-3d-regions")).toHaveCount(0);
  await expect.poll(async () => Number(await page.locator("#map-3d").getAttribute("data-region-mesh-count") || 0)).toBeGreaterThan(0);
  const layout = await (await page.request.get(`${baseURL}/api/books/${overview.book.id}/map-layout`)).json();
  const visibleRegionCount = layout.regions.filter((region) => Array.isArray(region.hull) && region.hull.length >= 3).length;
  const meshCount = Number(await page.locator("#map-3d").getAttribute("data-region-mesh-count") || 0);
  expect(meshCount).toBe(visibleRegionCount);
  await page.screenshot({ path: "output/playwright/v2.8-xiyouji-map-3d-regions.png", fullPage: false });
  await page.locator('.map-mode[data-mode="2d"]').click();
  await page.locator("#map-step-slider").evaluate((slider) => {
    slider.value = slider.max;
    slider.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await expect(page.locator("#map-step-count")).toHaveText(`${expectedSteps}/${expectedSteps}`);
  await expect(page.locator("#map-next")).toHaveText("已到末步");
  await expect(page.locator("#map-next")).toBeDisabled();
  expect(errors).toEqual([]);
});

test("十二部真实开放全文可以在三栏书库中检索、切换和打开", async ({ page }) => {
  test.skip(!process.env.NOVEL_ATLAS_REAL_CORPUS, "需要十二部本机开放全文验收副本");
  const errors = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(baseURL, { waitUntil: "networkidle" });
  await page.locator("#library-button").click();
  await expect(page.locator(".library-workspace")).toBeVisible();
  await expect(page.locator(".library-folder-pane")).toBeVisible();
  await expect(page.locator(".library-books-pane")).toBeVisible();
  await expect(page.locator(".library-detail-pane")).toBeVisible();
  await expect(page.locator(".library-folder-pane")).not.toContainText("children.get");
  await expect(page.locator(".library-folder-node")).not.toHaveCount(0);
  for (const title of ["西游记", "红楼梦", "水浒传", "三国演义", "聊斋志异", "镜花缘", "海上花列传", "Pride and Prejudice", "The Adventures of Sherlock Holmes", "A Princess of Mars", "銀河鉄道の夜", "The Spiraling Web"]) {
    await page.locator("#library-search").fill(title);
    await expect(page.locator(".library-book-card")).toHaveCount(1);
    await expect(page.locator(".library-book-card strong")).toHaveText(title);
    await expect(page.locator(".library-book-card")).toContainText("原文片段");
    await page.locator(".library-book-card").click();
    await expect(page.locator(".library-book-detail h3")).toHaveText(title);
    await expect(page.locator(".library-book-detail")).toContainText("原文片段");
    await expect(page.locator(".library-book-detail")).toContainText("真实开放作品");
    await expect(page.locator(".library-book-detail")).toContainText("分析范围");
    await expect(page.locator(".library-book-detail")).toContainText("查看作品来源");
  }
  await page.locator("#library-search").fill("红楼梦");
  await page.locator(".library-book-card").click();
  await page.locator(".library-detail-actions .open-book").click();
  await expect(page.locator("#book-select option:checked")).toHaveText("红楼梦");
  expect(errors).toEqual([]);
});

test("1366×768 地图右栏直接显示当前编年并与播放条同步", async ({ page }) => {
  const errors = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));

  await page.setViewportSize({ width: 1366, height: 768 });
  await page.goto(baseURL, { waitUntil: "networkidle" });
  await page.locator('.nav-item[data-view="map"]').click();
  await expect(page.locator(".map-context-rail")).toBeVisible();
  await expect(page.locator("#map-event-card h3")).toBeInViewport();
  await expect(page.locator("#map-evidence")).toBeInViewport();
  const evidenceBox = await page.locator("#map-evidence").boundingBox();
  expect(evidenceBox.y + evidenceBox.height).toBeLessThanOrEqual(768);
  const firstTitle = await page.locator("#map-event-card h3").textContent();
  await page.locator("#map-next").click();
  await expect(page.locator("#map-event-card h3")).not.toHaveText(firstTitle);
  await expect(page.locator('.map-chronology-item[aria-current="step"]')).toHaveCount(1);
  expect(errors).toEqual([]);
});

test("真实关系数据同时呈现单向和双向语义", async ({ page }) => {
  test.skip(!process.env.NOVEL_ATLAS_REAL_LONG, "需要本机真实长篇验收副本");
  const errors = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(baseURL, { waitUntil: "networkidle" });
  const overviewReady = page.waitForResponse((response) => /\/api\/books\/\d+\/overview/.test(response.url()) && response.ok());
  await selectBook(page, "西游记");
  const overview = await (await overviewReady).json();
  expect(overview.claims.filter((claim) => claim.directionality === "bidirectional").length).toBeGreaterThan(0);
  expect(overview.claims.filter((claim) => claim.directionality === "directed").length).toBeGreaterThan(0);
  const bidirectional = overview.claims.find((claim) => claim.directionality === "bidirectional");
  await page.locator('.nav-item[data-view="relationships"]').click();
  await expect(page.locator(".force-graph-shell")).toBeVisible();
  const bidirectionalText = page.locator(".fallback-list li").filter({ hasText: bidirectional.source_name }).filter({ hasText: bidirectional.target_name });
  await expect(bidirectionalText).not.toHaveCount(0);
  expect(await page.locator(".fallback-list li").filter({ hasText: "→" }).count()).toBeGreaterThan(0);
  expect(errors).toEqual([]);
});

test("三维关系图悬停人物时同步高亮并显示关系说明", async ({ page }) => {
  const errors = [];
  page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(baseURL, { waitUntil: "networkidle" });
  await selectBook(page, "长夜十二城 · 120章大型压力演示");
  await page.locator('.nav-item[data-view="relationships"]').click();
  await page.locator('.graph-mode[data-mode="3d"]').click();
  const label = page.locator(".relationship-label").first();
  await expect(label).toBeVisible({ timeout: 5000 });
  const name = await label.textContent();
  await label.hover();
  await expect(page.locator("#relationship-focus")).toContainText(name.trim());
  await expect(label).toHaveClass(/active/);
  expect(errors).toEqual([]);
});

test("故事分区在分析设置中可见且可切换阅读范围", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(baseURL, { waitUntil: "networkidle" });
  await selectBook(page, "长夜十二城 · 120章大型压力演示");
  await page.locator('.nav-item[data-view="collaboration"]').click();
  await expect(page.locator(".narrative-structure-manager")).toBeVisible();
  await expect(page.locator(".narrative-world-card")).not.toHaveCount(0);
  await expect(page.locator(".narrative-unit-card")).not.toHaveCount(0);
  await page.locator('.nav-item[data-view="map"]').click();
  const scope = page.locator("#story-scope-select");
  await expect(scope).toHaveCount(1);
  const unitValue = await scope.locator('option[value^="unit:"]').first().getAttribute("value");
  await chooseHiddenSelect(page, "#story-scope-select", unitValue);
  await expect(scope).toHaveValue(unitValue);
  await expect(page.locator("#map-step-count")).not.toHaveText("0/0");
});

test("共享表单弹窗保留输入、校验必填项并恢复触发焦点", async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 800 });
  await page.goto(baseURL, { waitUntil: "networkidle" });
  await page.locator("#library-button").focus();
  await page.evaluate(() => {
    window.__formResult = undefined;
    window.formAction({
      title: "核对说明",
      description: "浏览器交互回归",
      fields: [{ name: "reason", label: "判断依据", type: "textarea", required: true }],
    }).then((value) => { window.__formResult = value; });
  });
  await expect(page.locator("#form-dialog")).toHaveAttribute("open", "");
  await expect(page.locator('#form-dialog-fields [name="reason"]')).toBeFocused();
  await page.locator("#form-dialog-submit").click();
  await expect(page.locator("#form-dialog")).toHaveAttribute("open", "");
  await page.locator('#form-dialog-fields [name="reason"]').fill("原文证据支持该判断");
  await page.locator("#form-dialog-submit").click();
  await expect(page.locator("#form-dialog")).not.toHaveAttribute("open", "");
  await expect(page.locator("#library-button")).toBeFocused();
  expect(await page.evaluate(() => window.__formResult)).toEqual({ reason: "原文证据支持该判断" });
});

test("2.9 统一选择器、防剧透输入和质量任务页可直接操作", async ({ page }) => {
  const errors = [];
  page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(baseURL, { waitUntil: "networkidle" });

  await expect(page.locator("#sidebar-library-tree")).toBeVisible();
  await expect(page.locator("#book-select")).toBeHidden();
  const modelButton = page.locator("#provider-select").locator("xpath=..").locator(".select-box-button");
  await expect(modelButton).toBeVisible();
  await page.locator("#provider-select").evaluate((select) => {
    for (let index = 0; index < 9; index += 1) {
      const option = document.createElement("option");
      option.value = `provider-regression-${index}`;
      option.textContent = `长列表回归选项 ${index + 1}`;
      select.appendChild(option);
    }
  });
  await modelButton.click();
  await expect(page.locator(".select-popover")).toBeVisible();
  await expect(page.locator(".select-search")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.locator(".select-popover")).toHaveCount(0);

  await page.locator("#progress-count").focus();
  await page.keyboard.press("Enter");
  await expect(page.locator(".progress-inline-input")).toBeVisible();
  await page.locator(".progress-inline-input").fill("3");
  await page.keyboard.press("Enter");
  await expect(page.locator("#progress-count")).toHaveText(/第 3 章 · 共 \d+ 章/);

  await page.locator('.nav-item[data-view="quality"]').click();
  await expect(page.locator(".release-decision")).toBeVisible();
  await expect(page.locator('.quality-tab[data-tab="gold"]')).toHaveCount(0);
  await page.locator('.quality-tab[data-tab="cost"]').click();
  await expect(page.locator('.quality-tab[data-tab="cost"]')).toHaveClass(/active/);
  expect(errors).toEqual([]);
});

test("2.9.2 地图控制栏、区域联动和播放速度保持一致", async ({ page }) => {
  const errors = [];
  page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
  page.on("pageerror", (error) => errors.push(error.message));

  await page.setViewportSize({ width: 1366, height: 768 });
  await page.goto(baseURL, { waitUntil: "networkidle" });
  await selectBook(page, "长夜十二城 · 120章大型压力演示");
  await page.locator('.nav-item[data-view="map"]').click();

  const layoutResponse = await page.request.get(`${baseURL}/api/books/${await page.locator("#book-select").inputValue()}/map-layout`);
  const layout = await layoutResponse.json();
  expect(layout.region_coverage.generated_region_count).toBe(4);
  expect(layout.region_coverage.visible_region_count).toBe(4);
  await expect(page.locator(".semantic-region")).toHaveCount(4);
  expect((await page.locator(".semantic-region-label").allTextContents()).join(" ")).not.toContain("故事拓扑片区");

  const overviewResponse = await page.request.get(`${baseURL}/api/books/${await page.locator("#book-select").inputValue()}/overview?through_segment=119`);
  const overview = await overviewResponse.json();
  const regionByLocation = new Map();
  for (const region of layout.regions) for (const id of region.node_ids) if (!regionByLocation.has(Number(id))) regionByLocation.set(Number(id), String(region.id));
  const regionSteps = [];
  for (let index = 0; index < overview.events.length; index += 1) {
    const locationId = overview.events[index].location_entity_id;
    const regionId = regionByLocation.get(Number(locationId));
    if (regionId && !regionSteps.some((entry) => entry.regionId === regionId)) regionSteps.push({ index, regionId });
  }
  expect(regionSteps.length).toBeGreaterThan(1);
  for (const entry of regionSteps.slice(0, 2)) {
    await page.locator("#map-step-slider").evaluate((slider, value) => {
      slider.value = String(value);
      slider.dispatchEvent(new Event("input", { bubbles: true }));
    }, entry.index);
    await expect(page.locator(`.semantic-region[data-region="${entry.regionId}"]`)).toHaveAttribute("data-emphasis", "current");
    await expect(page.locator("#map-event-card")).toContainText("当前区域");
  }

  await page.locator(".map-view-menu summary").click();
  await page.locator("#map-fit-step").click();
  expect(await page.evaluate(() => window.localStorage.getItem("novel-atlas-map-camera-mode"))).toBe("step");
  const nextLocatedStep = overview.events.findIndex((event, index) => index > 0 && event.location_entity_id !== null);
  await page.locator("#map-step-slider").evaluate((slider, value) => {
    slider.value = String(value);
    slider.dispatchEvent(new Event("input", { bubbles: true }));
  }, nextLocatedStep);
  await expect.poll(async () => page.locator(".map-svg").evaluate((svg) => {
    const view = svg.getAttribute("viewBox").split(/\s+/).map(Number);
    const transform = svg.querySelector("#journey-avatar")?.getAttribute("transform") || "";
    const match = transform.match(/translate\(\s*([-\d.]+)[,\s]+([-\d.]+)\s*\)/);
    if (!match) return false;
    const x = Number(match[1]);
    const y = Number(match[2]);
    const safeX = view[2] * 0.28;
    const safeY = view[3] * 0.28;
    return x >= view[0] + safeX && x <= view[0] + view[2] - safeX
      && y >= view[1] + safeY && y <= view[1] + view[3] - safeY;
  })).toBe(true);

  await chooseHiddenSelect(page, "#map-playback-speed", "2");
  await expect(page.locator("#map-playback-speed")).toHaveValue("2");
  expect(await page.evaluate(() => window.localStorage.getItem("novel-atlas-playback-speed"))).toBe("2");

  const sizes = [
    { width: 1366, height: 768 },
    { width: 1440, height: 900 },
    { width: 1920, height: 1080 },
    { width: 720, height: 960 },
    { width: 390, height: 844 },
  ];
  const zooms = [0.8, 1, 1.25, 1.5];
  for (const size of sizes) {
    for (const zoom of zooms) {
      await page.setViewportSize({
        width: Math.max(260, Math.round(size.width / zoom)),
        height: Math.max(560, Math.round(size.height / zoom)),
      });
      const controls = page.locator(".map-control-deck");
      await expect(controls).toBeVisible();
      const boxes = await controls.locator(".map-view-toolbar, .map-viewport-tools").evaluateAll((elements) =>
        elements.map((element) => {
          const box = element.getBoundingClientRect();
          return { left: box.left, top: box.top, right: box.right, bottom: box.bottom };
        }),
      );
      expect(boxes).toHaveLength(2);
      const overlap = !(
        boxes[0].right <= boxes[1].left || boxes[1].right <= boxes[0].left
        || boxes[0].bottom <= boxes[1].top || boxes[1].bottom <= boxes[0].top
      );
      expect(overlap, `${size.width}x${size.height}@${zoom * 100}%`).toBe(false);
    }
  }

  await page.setViewportSize({ width: 1366, height: 768 });
  await page.locator('.map-mode[data-mode="3d"]').click();
  await expect.poll(async () => Number(await page.locator("#map-3d").getAttribute("data-region-mesh-count") || 0)).toBe(4);
  expect(errors).toEqual([]);
});

test("2.9.7 地图跟随任务、缩放记忆和二维三维相机记录隔离", async ({ page }) => {
  await page.addInitScript(() => {
    if (!window.sessionStorage.getItem("camera-e2e-reset")) {
      window.localStorage.clear();
      window.sessionStorage.setItem("camera-e2e-reset", "1");
    }
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(baseURL, { waitUntil: "networkidle" });
  await selectBook(page, "长夜十二城 · 120章大型压力演示");
  await page.locator('.nav-item[data-view="map"]').click();
  await expect(page.locator("#map-step-slider")).toBeVisible();

  await page.locator(".map-view-menu summary").click();
  await page.locator("#map-fit-follow").click();
  await expect(page.locator(".map-view-menu summary")).toHaveText("视角 · 跟随任务");

  const initialView = await page.locator(".map-svg").evaluate((svg) => svg.getAttribute("viewBox").split(/\s+/).map(Number));
  await page.locator("#map-zoom-in").click();
  await expect.poll(async () => page.locator(".map-svg").evaluate((svg) => Number(svg.getAttribute("viewBox").split(/\s+/)[2]))).toBeLessThan(initialView[2]);
  const zoomedView = await page.locator(".map-svg").evaluate((svg) => svg.getAttribute("viewBox").split(/\s+/).map(Number));

  const finalStep = Number(await page.locator("#map-step-slider").getAttribute("max"));
  await page.locator("#map-step-slider").evaluate((slider, value) => {
    slider.value = String(value);
    slider.dispatchEvent(new Event("input", { bubbles: true }));
  }, finalStep);
  await expect.poll(async () => page.locator(".map-svg").evaluate((svg) => {
    const view = svg.getAttribute("viewBox").split(/\s+/).map(Number);
    const match = (svg.querySelector("#journey-avatar")?.getAttribute("transform") || "").match(/translate\(\s*([-\d.]+)[,\s]+([-\d.]+)\s*\)/);
    if (!match) return false;
    const x = Number(match[1]);
    const y = Number(match[2]);
    const safeX = view[2] * 0.28;
    const safeY = view[3] * 0.28;
    return x >= view[0] + safeX && x <= view[0] + view[2] - safeX
      && y >= view[1] + safeY && y <= view[1] + view[3] - safeY;
  })).toBe(true);
  await expect.poll(async () => page.locator(".map-svg").evaluate((svg) => Number(svg.getAttribute("viewBox").split(/\s+/)[2]))).toBeCloseTo(zoomedView[2], 3);

  const records = await page.evaluate(() => JSON.parse(window.localStorage.getItem("novel-atlas-map-camera-state-v2")));
  const currentBookId = await page.locator("#book-select").inputValue();
  const twoDKey = Object.entries(records.entries)
    .filter(([key]) => key.startsWith(`${currentBookId}|`) && key.endsWith("|atlas|2d"))
    .sort((left, right) => Number(left[1].updatedAt || 0) - Number(right[1].updatedAt || 0))
    .at(-1)?.[0];
  expect(twoDKey).toBeTruthy();
  expect(records.entries[twoDKey].viewBox.width).toBeCloseTo(zoomedView[2], 3);

  await page.reload({ waitUntil: "networkidle" });
  await selectBook(page, "长夜十二城 · 120章大型压力演示");
  await page.locator('.nav-item[data-view="map"]').click();
  await expect(page.locator("#map-step-slider")).toBeVisible();
  await expect(page.locator(".map-view-menu summary")).toHaveText("视角 · 跟随任务");
  const restoredView = await page.locator(".map-svg").evaluate((svg) => svg.getAttribute("viewBox").split(/\s+/).map(Number));
  expect(restoredView[2]).toBeCloseTo(zoomedView[2], 3);
  expect(restoredView[0]).toBeCloseTo(zoomedView[0], 3);

  await page.locator('.map-mode[data-mode="3d"]').click();
  await expect(page.locator("#map-3d canvas")).toBeVisible();
  await page.locator("#map-zoom-in").click();
  await page.waitForTimeout(240);
  const after3D = await page.evaluate(() => JSON.parse(window.localStorage.getItem("novel-atlas-map-camera-state-v2")));
  expect(Object.keys(after3D.entries).some((key) => key.endsWith("|atlas|3d"))).toBe(true);
  await page.locator('.map-mode[data-mode="2d"]').click();
  await expect(page.locator(".map-svg")).toBeVisible();
  const backTo2D = await page.locator(".map-svg").evaluate((svg) => svg.getAttribute("viewBox").split(/\s+/).map(Number));
  expect(backTo2D[2]).toBeCloseTo(zoomedView[2], 3);
});

test("2.9.7 未知地点不移动相机并保持人物标记隐藏", async ({ page }) => {
  await page.addInitScript(() => window.localStorage.clear());
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.goto(baseURL, { waitUntil: "networkidle" });
  await page.locator('.nav-item[data-view="map"]').click();
  await expect(page.locator("#map-step-slider")).toBeVisible();
  const bookId = await page.locator("#book-select").inputValue();
  const overview = await page.request.get(`${baseURL}/api/books/${bookId}/overview`).then((response) => response.json());
  const unknownIndex = overview.events.findIndex((event, index) => event.location_entity_id === null && index > 0);
  test.skip(unknownIndex < 0, "当前回归书籍没有未知地点步骤");
  const previousIndex = overview.events.slice(0, unknownIndex).map((event, index) => ({ event, index })).reverse().find((item) => item.event.location_entity_id !== null)?.index;
  test.skip(previousIndex === undefined, "当前回归书籍没有未知地点前置步骤");
  await page.locator("#map-step-slider").evaluate((slider, value) => {
    slider.value = String(value);
    slider.dispatchEvent(new Event("input", { bubbles: true }));
  }, previousIndex);
  await page.waitForTimeout(760);
  const before = await page.locator(".map-svg").evaluate((svg) => svg.getAttribute("viewBox"));
  await page.locator("#map-step-slider").evaluate((slider, value) => {
    slider.value = String(value);
    slider.dispatchEvent(new Event("input", { bubbles: true }));
  }, unknownIndex);
  await expect(page.locator("#journey-avatar")).toHaveAttribute("hidden", "");
  await page.waitForTimeout(180);
  await expect(page.locator(".map-svg")).toHaveAttribute("viewBox", before);
});

test("2.9.7 详情关闭按钮和审核编辑区域保持间距", async ({ page }) => {
  await page.setViewportSize({ width: 1650, height: 982 });
  await page.goto(baseURL, { waitUntil: "networkidle" });
  await page.locator('.nav-item[data-view="relationships"]').click();
  await page.locator("details.fallback-list summary").click();
  await page.locator('.fallback-list li button[data-type="claim"]').first().click();
  await expect(page.locator("#inspector")).toHaveClass(/open/);
  const closeBox = await page.locator("#inspector-close").evaluate((element) => {
    const box = element.getBoundingClientRect();
    return { width: box.width, height: box.height, radius: getComputedStyle(element).borderRadius };
  });
  expect(closeBox.width).toBe(44);
  expect(closeBox.height).toBe(44);
  expect(closeBox.radius).toBe("999px");
  await page.locator(".relation-editor summary").click();
  const editorStyle = await page.locator(".relation-editor").evaluate((element) => ({
    gap: getComputedStyle(element.querySelector(".form-stack")).gap,
    padding: getComputedStyle(element.querySelector(".form-stack")).padding,
  }));
  expect(editorStyle.gap).toBe("12px");
  expect(editorStyle.padding).toBe("16px");
  const actions = await page.locator(".record-actions").evaluate((element) => getComputedStyle(element).justifyContent);
  expect(actions).toBe("center");

  await page.locator("#inspector-close").click();
  await page.locator('.nav-item[data-view="timeline"]').click();
  const memoryPanel = page.locator(".narrative-memory");
  if (await memoryPanel.count()) {
    await memoryPanel.locator("summary").click();
    const articleGap = await page.locator(".narrative-memory-content").evaluate((element) => {
      const items = [...element.querySelectorAll("article")].map((item) => item.getBoundingClientRect());
      return items.length > 1 ? items[1].top - items[0].bottom : 16;
    });
    expect(articleGap).toBeGreaterThanOrEqual(16);
  } else {
    await expect(page.locator(".timeline")).toBeVisible();
  }
});

test("分析设置双栏表单保留清晰间距", async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.goto(baseURL, { waitUntil: "networkidle" });
  await page.locator('.nav-item[data-view="collaboration"]').click();
  const geometry = await page.locator(".settings-card-grid > .settings-card").evaluateAll((cards) => {
    const boxes = cards.map((card) => card.getBoundingClientRect());
    const internalGaps = cards.map((card) => {
      const header = card.querySelector("header").getBoundingClientRect();
      const firstField = card.querySelector(".inline-control-form > :first-child").getBoundingClientRect();
      return firstField.top - header.bottom;
    });
    return { cardGap: boxes[1].left - boxes[0].right, internalGaps };
  });
  expect(geometry.cardGap).toBeGreaterThanOrEqual(16);
  expect(geometry.internalGaps.every((gap) => gap >= 16)).toBe(true);
  await page.locator(".settings-card-grid").screenshot({ path: "output/playwright/v2.9.7-settings-card-gap.png" });
  await page.setViewportSize({ width: 720, height: 960 });
  const verticalGap = await page.locator(".settings-card-grid > .settings-card").evaluateAll((cards) => {
    const boxes = cards.map((card) => card.getBoundingClientRect());
    return boxes[1].top - boxes[0].bottom;
  });
  expect(verticalGap).toBeGreaterThanOrEqual(16);
});

test("2.9.6 零问题时隐藏人工复核和专业工具", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(baseURL, { waitUntil: "networkidle" });
  await selectBook(page, "长夜十二城 · 120章大型压力演示");

  await page.locator('.nav-item[data-view="quality"]').click();
  await expect(page.locator('.quality-tab[data-tab="pending"]')).toBeVisible();
  await expect(page.locator('.quality-tab[data-tab="cost"]')).toBeVisible();
  await expect(page.locator('.quality-tab[data-tab="resolved"]')).toBeVisible();
  await expect(page.locator('.quality-tab[data-tab="gold"]')).toHaveCount(0);
  await expect(page.locator("#view-panel")).not.toContainText("人工金标准");
  await expect(page.locator("#view-panel")).not.toContainText("有效密封案例");
  await expect(page.locator("#metric-review-card")).toBeHidden();
  await expect(page.locator(".legacy-review-tools")).toHaveCount(0);
  await expect(page.locator("#view-panel")).not.toContainText("专业处理工具");
  await expect(page.locator("#view-panel")).not.toContainText("证据不足的身份");
  await expect(page.locator("body")).not.toContainText("可选人工复核");

  await page.locator("#library-button").click();
  await page.locator(".library-book-card").first().click();
  await page.locator(".edit-book").click();
  await expect(page.locator("#library-book-report-language")).toBeVisible();
  await chooseHiddenSelect(page, "#library-book-report-language", "en");
  await page.locator(".save-book").click();
  await expect(page.locator(".library-book-detail")).toContainText("English");

  await page.locator("#library-back").click();
  await page.locator('.nav-item[data-view="database"]').click();
  await expect(page.locator(".knowledge-workspace")).toBeVisible();
  await page.waitForTimeout(250);
  await page.locator(".knowledge-sidebar .world-create").evaluate((details) => { details.open = true; });
  const fields = page.locator(".knowledge-sidebar .world-create-form input, .knowledge-sidebar .world-create-form textarea, .knowledge-sidebar .world-create-form .select-box-button");
  expect(await fields.count()).toBeGreaterThanOrEqual(5);
  for (let index = 0; index < await fields.count(); index += 1) {
    await expect(fields.nth(index)).toBeVisible();
    const box = await fields.nth(index).boundingBox();
    expect(box.width).toBeGreaterThan(140);
  }
});

test("2.9.6 存在真实待处理身份时恢复核对入口", async ({ page }) => {
  await page.route(/\/api\/books\/\d+\/overview/, async (route) => {
    const response = await route.fetch();
    const overview = await response.json();
    overview.quality.unresolved_merges = 1;
    await route.fulfill({ response, json: overview });
  });

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(baseURL, { waitUntil: "networkidle" });
  await expect(page.locator("#metric-review-card")).toBeVisible();
  await expect(page.locator("#metric-review-card")).toContainText("需要核对身份");
  await page.locator('.nav-item[data-view="quality"]').click();
  await expect(page.locator(".legacy-review-tools")).toHaveCount(1);
  await expect(page.locator(".legacy-review-tools")).toContainText("专业处理工具");
});
