import { expect, test, type Page } from "@playwright/test";

// Synthetic map for interaction tests only; no game assets or rendering service.
const maps = ["one", "two"].map((id) => ({
  id,
  name: `Fixture ${id}`,
  widthMeters: 20000,
  heightMeters: 20000,
  image: "/maps/fixture.svg",
  grid: { columns: 2, rows: 2, offsetX: 10000, offsetY: 10000 },
}));
const image =
  '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><path fill="#315a6c" d="M0 0h64v64H0z"/><path fill="#83915d" d="M10 10h40v30H10z"/></svg>';

async function assets(page: Page, empty = false) {
  await page.route("**/maps/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/maps/manifest.json")
      return route.fulfill({ json: { schemaVersion: 1, maps: empty ? [] : maps } });
    if (path === "/maps/variants.json")
      return route.fulfill({
        json: {
          schemaVersion: 1,
          maps: Object.fromEntries(
            maps.map((map) => [
              map.id,
              [
                {
                  id: "satellite",
                  label: "Satellite",
                  image: "/maps/fixture.svg",
                  widthPixels: 64,
                  heightPixels: 64,
                  kind: "terrain",
                },
              ],
            ]),
          ),
        },
      });
    if (path === "/maps/fixture.svg")
      return route.fulfill({ contentType: "image/svg+xml", body: image });
    return route.fulfill({ status: 404, body: "No fixture for this optional resource" });
  });
}

test("Satellite starts, pans, switches maps, and opens panels", async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await assets(page);
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Satellite", exact: true })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.locator("#load-state")).toBeHidden();
  await expect(page.locator("#scale-label")).not.toHaveText("—");
  await expect(page.locator("#map-overlay, [data-tool]")).toHaveCount(0);
  const canvas = page.locator("#map-canvas");
  await canvas.focus();
  await page.keyboard.press("m");
  await page.keyboard.press("p");
  await expect(page.locator("#drawer")).toBeHidden();
  const box = (await canvas.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 60, box.y + box.height / 2 + 30, { steps: 5 });
  await page.mouse.up();
  await expect
    .poll(() => Number(new URLSearchParams(new URL(page.url()).hash.slice(1)).get("cx") ?? 0))
    .not.toBe(0);
  await canvas.press("0");
  await expect(page).toHaveURL(/cx=0&cz=0/);
  await page.locator("#places-open").click();
  await expect(page.locator("#drawer-title")).toHaveText("Places");
  await expect(page.getByText("No saved places on this map.")).toBeVisible();
  await page.locator("#drawer-close").click();
  await page.locator("#settings-open").click();
  await expect(page.locator("#drawer-title")).toHaveText("Settings");
  await page.locator("#drawer-close").click();
  await page.locator("#map-switcher").click();
  await page.getByRole("option", { name: "Fixture two" }).click();
  await expect(page.locator("#map-name")).toHaveText("Fixture two");
  await expect(page.getByRole("button", { name: "Satellite", exact: true })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect
    .poll(() =>
      canvas.evaluate((node) => {
        const c = node as HTMLCanvasElement;
        const pixel = c
          .getContext("2d")!
          .getImageData(c.width / 2 + 10, c.height / 2 + 10, 1, 1).data;
        return pixel[0] + pixel[1] + pixel[2] > 50;
      }),
    )
    .toBe(true);
  expect(errors).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("map.png") });
});

test("explicit view links survive reload", async ({ page }) => {
  await assets(page);
  await page.goto("/#m=two&v=original&cx=1200&cz=-800&k=2");
  await expect(page.getByRole("button", { name: "Builtin", exact: true })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page).toHaveURL(/cx=1200&cz=-800&k=2/);
  await page.reload();
  await expect(page.locator("#map-name")).toHaveText("Fixture two");
  await expect(page.getByRole("button", { name: "Builtin", exact: true })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
});

test("missing map data has an explicit empty state", async ({ page }) => {
  await assets(page, true);
  await page.goto("/");
  await expect(page.locator("#load-title")).toHaveText("No local map installed");
  await expect(page.locator("#load-detail")).toContainText("lists no maps");
});

test("temporary rulers use the footer scale and clear on exit", async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await assets(page);
  await page.goto("/");
  const toggle = page.getByRole("button", { name: "Temporary measurement", exact: true });
  await expect(toggle).toBeEnabled();
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  const canvas = page.locator("#map-canvas"),
    rulers = page.locator(".temporary-ruler");
  const box = (await canvas.boundingBox())!;
  const x = box.x + box.width * 0.3,
    y = box.y + box.height * 0.45;
  const drag = async (dx: number, dy: number) => {
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + dx, y + dy, { steps: 5 });
    await page.mouse.up();
  };
  await drag(120, 0);
  await expect(rulers).toHaveCount(1);
  await expect(page).toHaveURL(/cx=0&cz=0/);
  const label = await rulers.first().locator("text").textContent();
  const expectedKm = 120 / (Math.min(box.width, box.height) / 20000) / 1000;
  expect(label).toBe(`${expectedKm.toFixed(1)} km`);
  expect(Number(await rulers.first().locator("rect").getAttribute("width"))).toBeCloseTo(120);
  const footerWidth = await page
    .locator("#scale-line")
    .evaluate((e) => e.getBoundingClientRect().width);
  expect(Number(await page.locator("#ruler-stripes").getAttribute("width"))).toBeCloseTo(
    footerWidth / 2,
  );
  await drag(-40, 60);
  await expect(rulers).toHaveCount(2);
  await page.screenshot({ path: testInfo.outputPath("temporary-rulers.png") });
  await canvas.click({ button: "right" });
  await expect(rulers).toHaveCount(1);
  await expect(page.locator("#context-menu")).toBeHidden();
  await canvas.press("+");
  await expect
    .poll(async () => Number(await rulers.first().locator("rect").getAttribute("width")))
    .toBeGreaterThan(120);
  await expect(rulers.first().locator("text")).toHaveText(label!);
  await page.keyboard.press("Escape");
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  await expect(rulers).toHaveCount(0);
  await expect(page.locator("#ruler-hint")).toBeHidden();
  await toggle.click();
  await drag(70, 30);
  await expect(rulers).toHaveCount(1);
  await toggle.click();
  await expect(rulers).toHaveCount(0);
  await toggle.click();
  await expect(rulers).toHaveCount(0);
  await drag(50, 20);
  await page.locator("#map-switcher").click();
  await page.getByRole("option", { name: "Fixture two" }).click();
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  await expect(rulers).toHaveCount(0);
  await drag(40, 10);
  await expect
    .poll(() => Number(new URLSearchParams(new URL(page.url()).hash.slice(1)).get("cx") ?? 0))
    .not.toBe(0);
  await expect(rulers).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("unfinished drags and touch gestures stay temporary", async ({ page, isMobile }) => {
  await assets(page);
  await page.goto("/");
  const toggle = page.getByRole("button", { name: "Temporary measurement", exact: true });
  await expect(toggle).toBeEnabled();
  await toggle.click();
  const box = (await page.locator("#map-canvas").boundingBox())!;
  const x = box.x + box.width * 0.4,
    y = box.y + box.height * 0.5;
  const rulers = page.locator(".temporary-ruler");
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + 2, y);
  await page.mouse.up();
  await expect(rulers).toHaveCount(0);
  await page.mouse.down();
  await page.mouse.move(x + 70, y + 40, { steps: 4 });
  await expect(rulers).toHaveCount(1);
  await page.keyboard.press("Escape");
  await page.mouse.up();
  await toggle.click();
  await expect(rulers).toHaveCount(0);
  if (isMobile) {
    const cdp = await page.context().newCDPSession(page);
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] });
    await cdp.send("Input.dispatchTouchEvent", {
      type: "touchMove",
      touchPoints: [{ x: x + 70, y: y + 40 }],
    });
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await expect(rulers).toHaveCount(1);
  }
  await page.reload();
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  await expect(rulers).toHaveCount(0);
});

test("nearby endpoints show a combined total and keep each length", async ({ page }, testInfo) => {
  await assets(page);
  await page.goto("/");
  const toggle = page.getByRole("button", { name: "Temporary measurement", exact: true });
  await expect(toggle).toBeEnabled();
  await toggle.click();
  const canvas = page.locator("#map-canvas"),
    box = (await canvas.boundingBox())!;
  const x = box.x + box.width * 0.25,
    y = box.y + box.height * 0.5;
  const drag = async (sx: number, sy: number, ex: number, ey: number) => {
    await page.mouse.move(sx, sy);
    await page.mouse.down();
    await page.mouse.move(ex, ey, { steps: 5 });
    await page.mouse.up();
  };
  const rulers = page.locator(".temporary-ruler"),
    totals = page.locator(".ruler-total");
  await drag(x, y, x + 90, y);
  await expect(totals).toHaveCount(0);
  const individual = await rulers.first().locator("text").textContent();
  await drag(x + 97, y + 2, x + 97, y - 78);
  await expect(rulers).toHaveCount(2);
  await expect(page.locator(".ruler-label:not(.ruler-total)")).toHaveCount(2);
  await expect(rulers.first().locator("text")).toHaveText(individual!);
  const total = `Total ${(170 / (Math.min(box.width, box.height) / 20000) / 1000).toFixed(1)} km`;
  await expect(totals).toHaveText(total);
  expect(await totals.evaluate((node) => getComputedStyle(node).fontSize)).toBe("12px");
  await expect(rulers.first().locator("rect")).toHaveAttribute("height", "3");
  await drag(x, y + 150, x + 90, y + 150);
  await expect(rulers).toHaveCount(3);
  await expect(totals).toHaveCount(1);
  await page.screenshot({ path: testInfo.outputPath("ruler-total.png") });
  await canvas.press("+");
  await expect(totals).toHaveText(total);
  await canvas.click({ button: "right" });
  await expect(rulers).toHaveCount(2);
  await expect(totals).toHaveText(total);
  await canvas.click({ button: "right" });
  await expect(rulers).toHaveCount(1);
  await expect(totals).toHaveCount(0);
  await expect(rulers.first().locator("text")).toHaveText(individual!);
});
