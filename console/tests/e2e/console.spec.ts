import { expect, test } from "@playwright/test";

test("overview shows the demo workspace", async ({ page }) => {
  await page.goto("/console");
  await expect(page.getByText("Demo workspace: sample data")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await expect(page.getByText("Judgments this week")).toBeVisible();
});

test("playground: Lorde's reply is gate-usable; CuratorKIT drops the hallucinated answer", async ({ page }) => {
  await page.goto("/console/playground", { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "Judge", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Lorde", exact: true })).toBeVisible();
  await expect(page.getByText("✕ CuratorKIT drops the sample").first()).toBeVisible();
  await expect(page.getByText(/hallucination_contract_failed/).first()).toBeVisible();
});

test("review: keyboard labelling updates agreement", async ({ page }) => {
  await page.goto("/console/review", { waitUntil: "networkidle" });  // keyboard handlers attach after hydration
  await page.keyboard.press("1");          // 0.1 for the hallucinated answer
  await page.keyboard.press("Enter");
  await expect(page.getByText("1 labeled")).toBeVisible();
  await expect(page.getByText("100.0%")).toBeVisible();   // Lorde scored it 0.10: both reject
});

test("keys: a new key is shown once", async ({ page }) => {
  await page.goto("/console/keys", { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "Create key" }).click();
  await page.getByLabel("Name").fill("e2e");
  await page.getByRole("button", { name: "Create", exact: true }).click();
  await expect(page.getByText("Copy your new key now")).toBeVisible();
  await expect(page.locator("code").filter({ hasText: /^lorde_sk_[0-9A-Za-z]{43}$/ })).toBeVisible();
});

test("new job: threshold changes the preview", async ({ page }) => {
  await page.goto("/console/jobs/new", { waitUntil: "networkidle" });
  const preview = page.locator("aside p.tnum").first();
  const before = await preview.textContent();
  await page.getByLabel("Threshold").first().focus();
  await page.keyboard.press("End");          // slider to 0.95, as a keyboard user would
  await expect(preview).not.toHaveText(before ?? "");
});

test("job detail filters rows", async ({ page }) => {
  await page.goto("/console/jobs/2b8e4d70-5a9c-4f13-8e62-1d7c3b9a0f02");
  await page.waitForLoadState("networkidle");
  await page.getByLabel("Decision").selectOption("reject");
  await expect(page).toHaveURL(/decision=reject/);  // filters run on the server: wait for the filtered page
  await expect(page.getByText("Loading…")).toHaveCount(0);
  const decisions = page.locator("tbody td:nth-child(5)");
  await expect(decisions.first()).toContainText("Reject");
  expect(await decisions.filter({ hasText: "Pass" }).count()).toBe(0);
});

test("a page past the end is an empty page with a way back, not the error screen", async ({ page }) => {
  const r = await page.goto("/console/jobs?page=999");
  expect(r?.status()).toBe(200);
  await expect(page.getByText("Page 999 is past the end")).toBeVisible();
  await page.getByRole("link", { name: "Go to the last page" }).click();
  await expect(page.locator("tbody tr").first()).toBeVisible();
  await page.goto("/console/jobs/2b8e4d70-5a9c-4f13-8e62-1d7c3b9a0f02?page=5000");
  await expect(page.getByText("Page 5000 is past the end")).toBeVisible();
  await page.getByRole("link", { name: "Go to the last page" }).click();
  await expect(page.locator("tbody td:nth-child(5)").first()).toBeVisible();
});

test("datasets show a status; only ready ones can start a job", async ({ page }) => {
  await page.goto("/console/datasets");
  await expect(page.locator("tbody tr").first().getByText("Ready")).toBeVisible();
  // an upload this browser started whose file never arrived
  await page.evaluate(() => localStorage.setItem("lorde.pendingUploads", JSON.stringify({ d2: -Date.now() })));
  await page.goto("/console/datasets", { waitUntil: "networkidle" });
  const row = page.locator("tbody tr").filter({ hasText: "policy-qa-multilingual.parquet" });
  await expect(row.getByText("Upload incomplete")).toBeVisible();
  await expect(row.getByRole("link", { name: /Gate it/ })).toHaveCount(0);
  await page.goto("/console/jobs/new?dataset=d2", { waitUntil: "networkidle" });
  await expect(page.locator("#ds option[value=d2]")).toBeDisabled();
  await expect(page.locator("#ds")).not.toHaveValue("d2");
});

test("review: a labelled row cannot be saved twice", async ({ page }) => {
  await page.goto("/console/review", { waitUntil: "networkidle" });
  await expect(page.getByText(/^1 \/ \d+$/)).toBeVisible();
  await page.keyboard.press("3");
  await page.keyboard.press("Enter");
  await expect(page.getByText("1 labeled")).toBeVisible();
  await expect(page.getByText(/^2 \/ \d+$/)).toBeVisible();
  await page.keyboard.press("ArrowLeft");  // back to the row just labelled
  await expect(page.getByText("Already labelled: you gave 0.3.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save and next" })).toHaveCount(0);
  await page.keyboard.press("5");
  await page.keyboard.press("Enter");  // moves on without a second insert
  await expect(page.getByText(/^2 \/ \d+$/)).toBeVisible();
  await expect(page.getByText("1 labeled")).toBeVisible();
  await page.keyboard.press("ArrowLeft");
  await page.getByRole("button", { name: "Next unlabelled" }).click();
  await expect(page.getByText(/^2 \/ \d+$/)).toBeVisible();
  await expect(page.getByText("1 labeled")).toBeVisible();
});
