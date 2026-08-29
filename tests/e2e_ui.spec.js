const { test, expect } = require("playwright/test");

const baseURL = process.env.NOVEL_ATLAS_E2E_URL || "http://127.0.0.1:8766";

test("2D 与 3D 地图共享编年状态并支持快速连续切换", async ({ page }) => {
  const errors = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(baseURL, { waitUntil: "networkidle" });
  await expect(page.locator(".brand span")).toContainText("2.9.2");
  await page.locator('.nav-item[data-view="map"]').click();
  const totalSteps = Number(await page.locator("#map-step-slider").getAttribute("max")) + 1;
  const totalPlaces = await page.locator(".map-node").count();
  expect(totalSteps).toBeGreaterThan(1);
  expect(totalPlaces).toBeGreaterThan(0);
  await expect(page.locator("#map-step-count")).toHaveText(`1/${totalSteps}`);
  await expect(page.locator(".map-svg")).toBeVisible();
  expect(await page.locator(".semantic-region").count()).toBeGreaterThanOrEqual(1);

  for (let index = 0; index < 5; index += 1) {
    await page.locator("#map-next").dispatchEvent("click");
  }
  await expect(page.locator("#map-step-count")).toHaveText(`6/${totalSteps}`);
  const stepTitle = await page.locator("#map-event-card h3").textContent();

  await page.locator('.map-mode[data-mode="3d"]').click();
  await expect(page.locator("#map-step-count")).toHaveText(`6/${totalSteps}`);
  await expect(page.locator("#map-event-card h3")).toHaveText(stepTitle);
  await expect(page.locator("#map-3d canvas")).toBeVisible();
  for (let index = 0; index < 8; index += 1) {
    await page.locator("#map-next").dispatchEvent("click");
  }
  await expect(page.locator("#map-step-count")).toHaveText(`14/${totalSteps}`);

  await expect(page.locator(".map-3d-axis")).toContainText("平面方位未知");
  await page.screenshot({ path: "output/playwright/v2.9-map-3d.png", fullPage: true });
  await page.locator('.map-mode[data-mode="2d"]').click();
  await expect(page.locator("#map-step-count")).toHaveText(`14/${totalSteps}`);
  await expect(page.locator(".map-svg")).toBeVisible();
  await page.locator("#map-step-slider").evaluate((slider) => {
    slider.value = slider.max;
    slider.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await expect(page.locator("#map-step-count")).toHaveText(`${totalSteps}/${totalSteps}`);
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
  await page.locator("#book-select").selectOption({ label: "长夜十二城 · 120章大型压力演示" });
  await page.locator('.nav-item[data-view="database"]').click();
  await expect(page.locator(".knowledge-workspace")).toBeVisible();
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
  await page.route(/\/api\/books\/\d+\/concepts/, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 2500));
    await route.continue();
  });

  await page.locator("#book-select").selectOption({ label: "长夜十二城 · 120章大型压力演示" });
  await page.locator('.nav-item[data-view="database"]').click();
  await expect(page.locator(".concept-row")).toHaveCount(0, { timeout: 1000 });
  await expect(page.locator("#view-panel")).toContainText("正在读取这本书的知识结构");

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
  await page.locator("#book-select").selectOption({ label: "西游记" });
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
  await page.locator("#book-select").selectOption({ label: "西游记" });
  const overview = await (await overviewReady).json();
  expect(overview.claims.filter((claim) => claim.directionality === "bidirectional").length).toBeGreaterThan(0);
  expect(overview.claims.filter((claim) => claim.directionality === "directed").length).toBeGreaterThan(0);
  await page.locator('.nav-item[data-view="relationships"]').click();
  await expect(page.locator(".force-graph-shell")).toBeVisible();
  expect(await page.locator(".fallback-list li").filter({ hasText: "⇄" }).count()).toBeGreaterThan(0);
  expect(await page.locator(".fallback-list li").filter({ hasText: "→" }).count()).toBeGreaterThan(0);
  expect(errors).toEqual([]);
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

  const bookButton = page.locator("#book-select").locator("xpath=..").locator(".select-box-button");
  await expect(bookButton).toBeVisible();
  await page.locator("#book-select").evaluate((select) => {
    for (let index = 0; index < 9; index += 1) {
      const option = document.createElement("option");
      option.value = `escape-regression-${index}`;
      option.textContent = `长列表回归选项 ${index + 1}`;
      select.appendChild(option);
    }
  });
  await bookButton.click();
  await expect(page.locator(".select-popover")).toBeVisible();
  await expect(page.locator(".select-search")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.locator(".select-popover")).toHaveCount(0);

  await page.locator("#progress-count").focus();
  await page.keyboard.press("Enter");
  await expect(page.locator(".progress-inline-input")).toBeVisible();
  await page.locator(".progress-inline-input").fill("3");
  await page.keyboard.press("Enter");
  await expect(page.locator("#progress-count")).toHaveText(/3\/\d+/);

  await page.locator('.nav-item[data-view="quality"]').click();
  await expect(page.locator(".release-decision")).toBeVisible();
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
  await page.locator("#book-select").selectOption({ label: "长夜十二城 · 120章大型压力演示" });
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

  await page.locator("#map-playback-speed").selectOption("2");
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
