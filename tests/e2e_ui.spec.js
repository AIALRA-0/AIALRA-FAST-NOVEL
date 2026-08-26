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
  await expect(page.locator(".brand span")).toContainText("2.6.0");
  await page.locator('.nav-item[data-view="map"]').click();
  await expect(page.locator("#map-step-count")).toHaveText("1/120");
  await expect(page.locator(".map-svg")).toBeVisible();

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

  await page.screenshot({ path: "output/playwright/v2.6-map-3d.png", fullPage: true });
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
  await page.screenshot({ path: "output/playwright/v2.6-map-2d.png", fullPage: true });

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
  await page.screenshot({ path: "output/playwright/v2.6-mobile.png", fullPage: true });
  expect(errors).toEqual([]);
});
