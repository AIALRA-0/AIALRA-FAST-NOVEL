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
  await expect(page.locator(".brand span")).toContainText("2.7.0");
  await page.locator('.nav-item[data-view="map"]').click();
  await expect(page.locator("#map-step-count")).toHaveText("1/120");
  await expect(page.locator(".map-svg")).toBeVisible();
  await expect(page.locator(".semantic-region")).toHaveCount(4);

  for (let index = 0; index < 5; index += 1) {
    await page.locator("#map-next").dispatchEvent("click");
  }
  await expect(page.locator("#map-step-count")).toHaveText("6/120");
  const stepTitle = await page.locator("#map-event-card h3").textContent();

  await page.locator('.map-mode[data-mode="3d"]').click();
  await expect(page.locator("#map-step-count")).toHaveText("6/120");
  await expect(page.locator("#map-event-card h3")).toHaveText(stepTitle);
  await expect(page.locator("#map-3d canvas")).toBeVisible();
  for (let index = 0; index < 8; index += 1) {
    await page.locator("#map-next").dispatchEvent("click");
  }
  await expect(page.locator("#map-step-count")).toHaveText("14/120");

  await expect(page.locator(".map-3d-axis")).toContainText("平面方位未知");
  await page.screenshot({ path: "output/playwright/v2.7-map-3d.png", fullPage: true });
  await page.locator('.map-mode[data-mode="2d"]').click();
  await expect(page.locator("#map-step-count")).toHaveText("14/120");
  await expect(page.locator(".map-svg")).toBeVisible();
  await page.locator("#map-step-slider").evaluate((slider) => {
    slider.value = slider.max;
    slider.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await expect(page.locator("#map-step-count")).toHaveText("120/120");
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
  expect(visiblePlaces).toBe(24);
  await page.screenshot({ path: "output/playwright/v2.7-map-2d.png", fullPage: true });

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
  await expect(page.locator("#map-next")).toHaveCSS("min-height", "40px");
  await page.locator("#map-next").focus();
  const outlineWidth = await page.locator("#map-next").evaluate((element) => getComputedStyle(element).outlineWidth);
  expect(Number.parseFloat(outlineWidth)).toBeGreaterThanOrEqual(3);
  await page.locator('.map-mode[data-mode="3d"]').click();
  await expect(page.locator("#map-3d canvas")).toBeVisible();
  await page.screenshot({ path: "output/playwright/v2.7-mobile.png", fullPage: true });
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
  await page.locator('.nav-item[data-view="database"]').click();
  await expect(page.locator(".knowledge-workspace")).toBeVisible();
  await page.locator("#entry-search").fill("核心危机");
  await expect(page.locator(".concept-row")).toHaveCount(1);
  await page.locator(".concept-row").click();
  await expect(page.locator("#inspector")).toHaveClass(/open/);
  await expect(page.locator(".knowledge-claim")).toHaveCount(1);
  await expect(page.locator(".knowledge-history")).toBeVisible();
  const titleBox = await page.locator("h1").boundingBox();
  expect(titleBox.width).toBeGreaterThan(180);
  expect(titleBox.height).toBeLessThan(180);
  await page.screenshot({ path: "output/playwright/v2.7-knowledge.png", fullPage: true });
  expect(errors).toEqual([]);
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
  await page.locator("#book-select").selectOption({ label: "西游记" });
  await page.waitForLoadState("networkidle");
  await page.locator('.nav-item[data-view="map"]').click();
  await expect(page.locator("#map-step-count")).toHaveText("1/1049");
  const nodeCount = await page.locator(".map-node").count();
  const retainedLabels = await page.locator(".map-node-label").count();
  const collisionHiddenLabels = await page.locator(".map-node-label-collided").count();
  expect(nodeCount).toBeGreaterThan(80);
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
  await page.screenshot({ path: "output/playwright/v2.7-xiyouji-map-v23.png", fullPage: false });
  expect(visibleLabelOverlaps).toEqual([]);

  const title = await page.locator("#map-event-card h3").textContent();
  await page.locator('.map-mode[data-mode="3d"]').click();
  await expect(page.locator("#map-step-count")).toHaveText("1/1049");
  await expect(page.locator("#map-event-card h3")).toHaveText(title);
  await page.locator('.map-mode[data-mode="2d"]').click();
  await page.locator("#map-step-slider").evaluate((slider) => {
    slider.value = slider.max;
    slider.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await expect(page.locator("#map-step-count")).toHaveText("1049/1049");
  await expect(page.locator("#map-next")).toHaveText("已到末步");
  await expect(page.locator("#map-next")).toBeDisabled();
  expect(errors).toEqual([]);
});
