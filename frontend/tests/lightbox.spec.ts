import { expect, test, type Page } from "@playwright/test";
import type { Tweet } from "../src/types";

const user = { id: 1, username: "viewer_test", display_name: "Viewer test", avatar_url: null, created_at: "2026-01-01T00:00:00Z" };
const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=", "base64");
const posts: Tweet[] = Array.from({ length: 8 }, (_, i) => ({
  id: i + 1, author: user, content: i === 3 ? "https://images.example.test/inline.png" : `Post ${i + 1}\n` + "A paragraph in the feed.\n".repeat(4),
  media_urls: i === 2 ? ["/uploads/first.png", "/uploads/second.png"] : [],
  media_alts: i === 2 ? ["First photo", "Second photo"] : [],
  created_at: "2026-01-02T00:00:00Z", edited_at: null, like_count: 0, comment_count: 0,
  retweet_count: 0, view_count: 0, liked_by_me: false, quoted_post: null, visibility: "public",
}));

async function setup(page: Page, path = "/") {
  let timelineRequests = 0;
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/stream")) return route.abort();
    let json: unknown = [];
    if (path.endsWith("/auth/me")) json = user;
    else if (path.includes("/timeline/")) {
      timelineRequests++;
      json = { items: posts, next_cursor: null };
    } else if (path.endsWith("/unread-count")) json = { count: 0 };
    else if (path.endsWith("/tweets/3")) json = posts[2];
    else if (path.endsWith("/profile")) json = { ...user, bio: "", follower_count: 0, following_count: 0, post_count: 8, is_current_user: true, is_following: false };
    else if (route.request().method() !== "GET" && !path.endsWith("/views") && !path.endsWith("/stats"))
      throw new Error(`Unexpected write ${path}`);
    await route.fulfill({ json });
  });
  await page.route("**/uploads/*.png", (route) => route.fulfill({ contentType: "image/png", body: png }));
  await page.route("https://images.example.test/inline.png", (route) => route.fulfill({ contentType: "image/png", body: png }));
  await page.goto(path);
  await expect(page.locator(".media-grid").first()).toBeVisible();
  return () => timelineRequests;
}

async function openPhoto(page: Page) {
  const photo = page.getByRole("button", { name: "First photo", exact: true });
  await expect(photo).toBeVisible();
  await photo.scrollIntoViewIfNeeded();
  const y = await page.evaluate(() => window.scrollY);
  await photo.click();
  await expect(page.getByRole("dialog", { name: "Image viewer" })).toBeVisible();
  return y;
}

test("Back closes the viewing session without leaving or reloading the scrolled feed", async ({ page }) => {
  const requests = await setup(page);
  await Promise.all([
    page.waitForResponse((response) => response.url().includes("/timeline/home")),
    page.getByRole("tab", { name: "Following", exact: true }).click(),
  ]);
  await expect(page).toHaveURL(/\/following$/);
  await expect(page.getByRole("button", { name: "Refresh timeline" })).toBeEnabled();
  const beforeRequests = requests();
  const y = await openPhoto(page);
  await expect(page).toHaveURL(/\/following$/);
  await page.getByRole("button", { name: "Next image" }).click();
  await expect(page.locator(".lightbox-image")).toHaveAttribute("alt", "Second photo");
  await page.goBack();
  await expect(page.getByRole("dialog", { name: "Image viewer" })).toBeHidden();
  await expect(page).toHaveURL(/\/following$/);
  await expect.poll(() => page.evaluate(() => scrollY)).toBeCloseTo(y, 0);
  expect(requests()).toBe(beforeRequests);
  expect(await page.evaluate(() => document.body.style.overflow)).not.toBe("hidden");
  // The next Back leaves the feed normally; Forward restores the viewer too.
  await page.goBack();
  await expect(page).toHaveURL(/\/$/);
  await page.goForward();
  await expect(page).toHaveURL(/\/following$/);
  await expect(page.getByRole("dialog", { name: "Image viewer" })).toBeHidden();
  await page.goForward();
  await expect(page.getByRole("dialog", { name: "Image viewer" })).toBeVisible();
  await page.goBack();
  await expect(page.getByRole("dialog", { name: "Image viewer" })).toBeHidden();
});

test("closing with X, Escape or the backdrop consumes the same history entry", async ({ page }) => {
  await setup(page);
  await Promise.all([
    page.waitForResponse((response) => response.url().includes("/timeline/home")),
    page.getByRole("tab", { name: "Following", exact: true }).click(),
  ]);
  const index = await page.evaluate(() => history.state.idx);
  for (const mode of ["button", "escape", "backdrop"]) {
    await openPhoto(page);
    if (mode === "button") await page.getByRole("button", { name: "Close image viewer" }).click();
    else if (mode === "escape") await page.keyboard.press("Escape");
    else await page.locator(".lightbox").click({ position: { x: 10, y: 100 } });
    await expect(page.getByRole("dialog", { name: "Image viewer" })).toBeHidden();
    await expect.poll(() => page.evaluate(() => history.state.idx)).toBe(index);
  }
  await page.goBack();
  await expect(page).toHaveURL(/\/$/);
});

test("Back also closes inline images and images opened on a detail page", async ({ page }) => {
  await setup(page);
  await page.locator("#post-4 .tweet-media-link").click();
  await expect(page.getByRole("dialog", { name: "Image viewer" })).toBeVisible();
  await page.goBack();
  await expect(page.getByRole("dialog", { name: "Image viewer" })).toBeHidden();
  await expect(page).toHaveURL(/\/$/);
  await page.goto("/tweet/3");
  await openPhoto(page);
  await page.goBack();
  await expect(page.getByRole("dialog", { name: "Image viewer" })).toBeHidden();
  await expect(page).toHaveURL(/\/tweet\/3$/);
});
