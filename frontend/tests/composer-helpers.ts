import { expect, type Page } from "@playwright/test";

export async function openComposer(page: Page) {
  await expect(page.locator(".app-shell")).toBeVisible();
  const entry = page.locator(".mobile-compose-button");
  if (await entry.isVisible()) {
    await entry.click();
    await expect(page.getByRole("dialog", { name: "Compose post", exact: true })).toBeVisible();
  }
}

export async function closeComposer(page: Page) {
  const close = page.getByRole("button", { name: "Close composer", exact: true });
  if (await close.isVisible()) {
    await close.click();
    await expect(page.getByRole("dialog", { name: "Compose post", exact: true })).toBeHidden();
  }
}
