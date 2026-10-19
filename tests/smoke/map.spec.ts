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
  const painted = await canvas.evaluate((node) => {
    const c = node as HTMLCanvasElement;
    const pixel = c.getContext("2d")!.getImageData(c.width / 2 + 10, c.height / 2 + 10, 1, 1).data;
    return pixel[0] + pixel[1] + pixel[2] > 50;
  });
  expect(painted).toBe(true);
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
