import { expect, test } from "@playwright/test";

test("landing renders and the hamburger menu opens, expands and closes", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("The judge for");
  const menu = page.getByRole("dialog", { name: "Menu" });
  // A click that lands before hydration is lost (the dev server hydrates late under parallel tests), so retry it.
  await expect(async () => {
    await page.getByRole("button", { name: "Open menu" }).click();
    await expect(menu).toBeVisible({ timeout: 1000 });
  }).toPass({ timeout: 15_000 });
  await expect(menu.getByRole("link", { name: /Lorde Bench/ })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
  await expect(page.getByRole("button", { name: "Open menu" })).toBeFocused();
});

test("every marketing page is its own route", async ({ page }) => {
  for (const [path, heading] of [["/docs", "five minutes"], ["/bench", "must prove"], ["/model", "trained to judge"], ["/status", "System status"]]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toContainText(heading);
  }
  const r = await page.goto("/no-such-page");
  expect(r?.status()).toBe(404);
});

test("code tabs switch", async ({ page }) => {
  await page.goto("/docs");
  await page.getByRole("tab", { name: "CuratorKIT" }).first().click();
  await expect(page.getByRole("tabpanel").first()).toContainText("judge_llm=judge_llm()");
});

test("the menu closes after a same-page hash link", async ({ page }) => {
  await page.goto("/docs");
  const menu = page.getByRole("dialog", { name: "Menu" });
  await expect(async () => {
    await page.getByRole("button", { name: "Open menu" }).click();
    await expect(menu).toBeVisible({ timeout: 1000 });
  }).toPass({ timeout: 15_000 });
  const link = menu.getByRole("link", { name: /API reference/ });
  if (!(await link.isVisible())) await menu.getByRole("button", { name: "Developers" }).click();  // phone: groups collapse
  await link.click();
  await expect(page).toHaveURL(/\/docs#api$/);
  await expect(menu).toBeHidden();
  await expect(page.locator("#api")).toBeInViewport();
});
