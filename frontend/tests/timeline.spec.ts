import { expect, test, type Page, type Route } from "@playwright/test";
import type { Tweet } from "../src/types";

const user = {
  id: 1,
  username: "refresh_test",
  display_name: "Refresh test",
  created_at: "2026-01-01T00:00:00Z",
  avatar_url: null,
};
const png = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
);
function tweet(id: number): Tweet {
  return {
    id,
    author: user,
    content:
      `Timeline post ${id}\n` + "A paragraph in the timeline.\n".repeat(4),
    media_urls: [],
    media_alts: [],
    created_at: "2026-01-02T00:00:00Z",
    edited_at: null,
    like_count: 0,
    comment_count: 0,
    retweet_count: 0,
    view_count: 0,
    liked_by_me: false,
    quoted_post: null,
    visibility: "public",
  };
}
const oldPosts = Array.from({ length: 10 }, (_, i) => tweet(i + 1));
const result = (items = oldPosts, cursor: string | null = null) => ({
  items,
  next_cursor: cursor,
  strategy: "for_you",
});

async function setup(
  page: Page,
  handler: (route: Route, url: URL) => Promise<void>,
) {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.includes("/timeline/")) return handler(route, url);
    if (path.endsWith("/stream")) return route.abort();
    let json: unknown = [];
    if (path.endsWith("/auth/me")) json = user;
    else if (path.endsWith("/unread-count")) json = { count: 0 };
    else if (path.endsWith("/media"))
      json = { url: "/uploads/refresh-test.png" };
    else if (path.endsWith("/tweets") && route.request().method() === "POST")
      json = { ...tweet(500), content: route.request().postDataJSON().content };
    else if (
      route.request().method() !== "GET" &&
      !path.endsWith("/tweets/stats") &&
      !path.endsWith("/tweets/views")
    )
      throw new Error(`Unexpected write ${path}`);
    await route.fulfill({ json });
  });
  await page.route("**/uploads/refresh-test.png", (route) =>
    route.fulfill({ contentType: "image/png", body: png }),
  );
  await page.goto("/");
  await expect(page.locator("#post-1")).toBeVisible();
}

test("refresh replaces pagination while preserving the draft and attached image", async ({
  page,
}) => {
  let refreshed = false;
  const cursors: (string | null)[] = [];
  await setup(page, async (route, url) => {
    const cursor = url.searchParams.get("cursor");
    cursors.push(cursor);
    const data =
      cursor === "old-page"
        ? result([tweet(20)])
        : cursor === "new-page"
          ? result([tweet(101)])
          : refreshed
            ? result([tweet(100), ...oldPosts], "new-page")
            : result(oldPosts, "old-page");
    await route.fulfill({ json: data });
  });
  const field = page.getByRole("textbox", { name: "Tweet content" });
  await field.fill("Keep this draft during refresh.");
  await page
    .locator(".composer input[type=file]")
    .setInputFiles({ name: "draft.png", mimeType: "image/png", buffer: png });
  await expect(page.locator(".composer-media img")).toHaveCount(1);
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await expect(page.locator("#post-20")).toBeVisible();
  refreshed = true;
  await page.getByRole("button", { name: "Refresh timeline" }).click();
  await expect(page.locator("#post-100")).toBeVisible();
  await expect(page.locator("#post-20")).toHaveCount(0);
  await expect(field).toHaveValue("Keep this draft during refresh.");
  await expect(page.locator(".composer-media img")).toHaveCount(1);
  await expect.poll(() => page.evaluate(() => scrollY)).toBe(0);
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await expect(page.locator("#post-101")).toBeVisible();
  expect(cursors.slice(-2)).toEqual([null, "new-page"]);
});

test("failed refresh keeps content and supports retry without duplicate requests", async ({
  page,
}) => {
  let held: Route | undefined;
  let hold = false;
  let requests = 0;
  await setup(page, async (route) => {
    requests++;
    if (hold) held = route;
    else await route.fulfill({ json: result() });
  });
  const field = page.getByRole("textbox", { name: "Tweet content" });
  await field.fill("Draft survives a failed request");
  hold = true;
  const before = requests;
  const refresh = page.getByRole("button", { name: "Refresh timeline" });
  await refresh.click();
  await expect(refresh).toBeDisabled();
  await expect.poll(() => Boolean(held)).toBe(true);
  await refresh.evaluate((el) => {
    (el as HTMLButtonElement).click();
  });
  expect(requests).toBe(before + 1);
  await held!.fulfill({
    status: 503,
    json: { detail: "Temporarily unavailable" },
  });
  await expect(page.getByRole("alert")).toContainText(
    "Temporarily unavailable",
  );
  await expect(page.locator("#post-1")).toBeVisible();
  await expect(field).toHaveValue("Draft survives a failed request");
  held = undefined;
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect.poll(() => Boolean(held)).toBe(true);
  await held!.fulfill({ json: result([tweet(100)]) });
  await expect(page.locator("#post-100")).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("switching tabs discards a late refresh and Following bypasses its first-page cache", async ({
  page,
}) => {
  let hold = false;
  let held: Route | undefined;
  const followingRequests: URL[] = [];
  await setup(page, async (route, url) => {
    if (url.pathname.endsWith("/home")) {
      followingRequests.push(url);
      await route.fulfill({ json: result([tweet(200)]) });
    } else if (hold) held = route;
    else await route.fulfill({ json: result() });
  });
  hold = true;
  await page.getByRole("button", { name: "Refresh timeline" }).click();
  await expect.poll(() => Boolean(held)).toBe(true);
  await page.getByRole("tab", { name: "Following", exact: true }).click();
  await expect(page.locator("#post-200")).toBeVisible();
  await expect.poll(() => held!.request().failure()).not.toBeNull();
  await held!.fulfill({ json: result([tweet(999)]) });
  await expect(page.locator("#post-999")).toHaveCount(0);
  await page.getByRole("button", { name: "Refresh timeline" }).click();
  await expect
    .poll(() => followingRequests.at(-1)?.searchParams.get("refresh"))
    .toBe("true");
  expect(followingRequests.at(-1)?.searchParams.has("cursor")).toBe(false);
});

test("refresh cancels an older pagination request", async ({ page }) => {
  let held: Route | undefined;
  let refreshed = false;
  await setup(page, async (route, url) => {
    if (url.searchParams.has("cursor")) held = route;
    else
      await route.fulfill({
        json: refreshed ? result([tweet(100)]) : result(oldPosts, "older"),
      });
  });
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await expect.poll(() => Boolean(held)).toBe(true);
  refreshed = true;
  await page.getByRole("button", { name: "Refresh timeline" }).click();
  await expect(page.locator("#post-100")).toBeVisible();
  await expect.poll(() => held!.request().failure()).not.toBeNull();
  await held!.fulfill({ json: result([tweet(999)]) });
  await expect(page.locator("#post-999")).toHaveCount(0);
});

test("publishing during refresh keeps the newly posted tweet", async ({
  page,
}) => {
  let held: Route | undefined;
  let hold = false;
  await setup(page, async (route) => {
    if (hold) held = route;
    else await route.fulfill({ json: result() });
  });
  await page
    .getByRole("textbox", { name: "Tweet content" })
    .fill("A post sent while refreshing");
  hold = true;
  await page.getByRole("button", { name: "Refresh timeline" }).click();
  await expect.poll(() => Boolean(held)).toBe(true);
  await page
    .locator(".composer")
    .getByRole("button", { name: "Post", exact: true })
    .click();
  await expect(page.locator("#post-500")).toBeVisible();
  await held!.fulfill({ json: result() });
  await expect(page.locator("#post-500")).toBeVisible();
});

test("mobile pull refresh has a threshold and excludes normal scrolling and text entry", async ({
  page,
  context,
  isMobile,
}) => {
  test.skip(!isMobile, "Touch gesture");
  let requests = 0;
  await setup(page, async (route) => {
    requests++;
    await route.fulfill({ json: result() });
  });
  const touch = await context.newCDPSession(page);
  async function pull(
    x: number,
    y: number,
    dx: number,
    dy: number,
    cancel = false,
  ) {
    await touch.send("Input.dispatchTouchEvent", {
      type: "touchStart",
      touchPoints: [{ x, y }],
    });
    for (let step = 1; step <= 8; step++) {
      await touch.send("Input.dispatchTouchEvent", {
        type: "touchMove",
        touchPoints: [{ x: x + (dx * step) / 8, y: y + (dy * step) / 8 }],
      });
    }
    await touch.send("Input.dispatchTouchEvent", {
      type: cancel ? "touchCancel" : "touchEnd",
      touchPoints: [],
    });
  }
  const baseline = requests;
  await pull(190, 300, 0, 40);
  await pull(190, 300, 100, 15);
  await pull(190, 300, 0, 160, true);
  expect(requests).toBe(baseline);
  const field = page.getByRole("textbox", { name: "Tweet content" });
  const box = (await field.boundingBox())!;
  await pull(box.x + 20, box.y + 20, 0, 160);
  await field.blur();
  expect(requests).toBe(baseline);
  await page.evaluate(() => window.scrollTo(0, 400));
  await pull(190, 300, 0, 160);
  expect(requests).toBe(baseline);
  await page.evaluate(() => window.scrollTo(0, 0));
  await pull(190, 300, 0, 160);
  await expect.poll(() => requests).toBe(baseline + 1);
  await expect(page.locator(".home-feed [role=status]")).toHaveText(
    "Timeline refreshed",
  );
});
