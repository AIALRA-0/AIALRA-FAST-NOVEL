const { test, expect } = require("playwright/test");

const baseURL = process.env.NOVEL_ATLAS_E2E_URL;

async function selectSyntheticBook(page) {
  await page.locator("#book-select").evaluate((select) => {
    const option = [...select.options].find((item) => item.textContent === "长夜十二城 · 120章大型压力演示");
    if (!option) throw new Error("找不到内置合成长篇演示");
    select.value = option.value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await expect(page.locator("#book-title")).toContainText("长夜十二城");
}

async function sanitizePublicScreenshot(page) {
  await page.evaluate(() => {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      node.textContent = node.textContent
        .replace(/\b\d+\s*\/\s*\d+\b/g, "已脱敏进度")
        .replace(/\b\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\b/g, "已脱敏时间");
    }
  });
}

test("生成 README 脱敏实机截图", async ({ page }) => {
  test.skip(process.env.CAPTURE_README_SCREENSHOTS !== "1" || !baseURL, "仅在维护者主动指定脱敏截图环境时运行");
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(baseURL, { waitUntil: "networkidle" });
  await selectSyntheticBook(page);

  await page.locator('.nav-item[data-view="relationships"]').click();
  await expect(page.locator(".force-graph-shell")).toBeVisible();
  await page.waitForTimeout(1200);
  await sanitizePublicScreenshot(page);
  await page.screenshot({ path: "docs/assets/novel-atlas-relationships.png" });

  await page.locator('.nav-item[data-view="map"]').click();
  await expect(page.locator(".map-svg")).toBeVisible();
  await page.locator("#map-step-slider").evaluate((slider) => {
    slider.value = String(Math.min(Number(slider.max), 41));
    slider.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await page.waitForTimeout(300);
  await sanitizePublicScreenshot(page);
  await page.screenshot({ path: "docs/assets/novel-atlas-map-2d.png" });

  await page.locator('.map-mode[data-mode="3d"]').click();
  await expect(page.locator("#map-3d canvas")).toBeVisible();
  await page.waitForTimeout(900);
  await sanitizePublicScreenshot(page);
  await page.screenshot({ path: "docs/assets/novel-atlas-map-3d.png" });

  await page.locator('.nav-item[data-view="database"]').click();
  await expect(page.locator(".knowledge-workspace")).toBeVisible();
  await page.locator("#entry-search").fill("核心危机");
  await page.locator(".concept-row").first().click();
  await expect(page.locator("#inspector")).toHaveClass(/open/);
  await sanitizePublicScreenshot(page);
  await page.screenshot({ path: "docs/assets/novel-atlas-knowledge.png" });

  await page.locator("#inspector-close").click();
  await page.locator("#library-button").click();
  await expect(page.locator(".library-workspace")).toBeVisible();
  await sanitizePublicScreenshot(page);
  await page.screenshot({ path: "docs/assets/novel-atlas-library.png" });

  await page.locator("#library-back").click();
  await page.locator('.nav-item[data-view="quality"]').click();
  await expect(page.locator(".release-decision")).toBeVisible();
  await expect(page.locator(".legacy-review-tools")).toHaveCount(0);
  await sanitizePublicScreenshot(page);
  await page.screenshot({ path: "docs/assets/novel-atlas-quality.png" });
});
