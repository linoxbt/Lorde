import { expect, test } from "@playwright/test";

test("console navigation through the hamburger on a phone", async ({ page }) => {
  await page.goto("/console");
  await page.getByRole("button", { name: "Open menu" }).click();
  const menu = page.getByRole("dialog", { name: "Console" });
  await expect(menu).toBeVisible();
  await menu.getByRole("link", { name: "API keys" }).click();
  await expect(page).toHaveURL(/\/console\/keys$/);
  await expect(page.getByRole("heading", { name: "API keys" })).toBeVisible();
});
