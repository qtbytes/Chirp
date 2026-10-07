import { expect, test, type Page } from "@playwright/test";

async function openApp(page: Page, moderator = true, path = "/") {
  const user = {
    id: 1,
    username: "nav_test",
    display_name: "Navigation test",
    created_at: "2026-01-01T00:00:00Z",
    avatar_url: null,
    is_moderator: moderator,
  };
  const emptyPage = { items: [], next_cursor: null };
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    let json: unknown = [];
    if (path === "/auth/me") json = user;
    else if (path === "/notifications/unread-count") json = { count: 2 };
    else if (path === "/dm/unread-count") json = { count: 1 };
    else if (path === "/notifications/stream") {
      await route.abort();
      return;
    } else if (path.endsWith("/profile"))
      json = {
        ...user,
        bio: "",
        follower_count: 0,
        following_count: 0,
        post_count: 0,
        is_current_user: true,
        is_following: false,
        dm_policy: "everyone",
        email: null,
        pending_email: null,
      };
    else if (path.startsWith("/dm/with/") && route.request().method() === "GET")
      json = {
        other_user: { ...user, id: 2, username: "friend" },
        messages: [],
        next_cursor: null,
        can_send: true,
        cannot_send_reason: null,
        muted: false,
        blocked: false,
      };
    else if (path === "/auth/logout" || path.endsWith("/read")) {
      await route.fulfill({ status: 204 });
      return;
    } else if (
      path.startsWith("/timeline/") ||
      path.endsWith("/tweets") ||
      [
        "/notifications",
        "/dm/conversations",
        "/moderation/reports",
        "/blocks",
        "/mutes",
      ].includes(path)
    )
      json = emptyPage;
    else if (route.request().method() !== "GET")
      throw new Error(`Unexpected write: ${path}`);
    await route.fulfill({ json });
  });
  await page.goto(path);
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
}

test.describe("mobile navigation", () => {
  test.beforeEach(({ isMobile }) => test.skip(!isMobile, "Mobile layout"));

  test("keeps four destinations usable across phone widths and highlights the current route", async ({
    page,
  }) => {
    await openApp(page);
    const nav = page.getByRole("navigation", { name: "Primary" });
    for (const width of [320, 360, 393, 430]) {
      await page.setViewportSize({ width, height: 800 });
      await expect(nav.getByRole("link")).toHaveCount(4);
      await expect(nav.getByRole("link", { name: "Settings" })).toHaveCount(0);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      const rail = await page.locator(".rail").boundingBox();
      expect(rail!.x).toBeGreaterThan(0);
      expect(rail!.x + rail!.width).toBeLessThan(width);
      expect(rail!.y + rail!.height).toBeLessThan(800);
    }
    for (const [name, path] of [
      ["Search", "/search"],
      ["Notifications, 2 unread", "/notifications"],
      ["Messages, 1 unread", "/messages"],
      ["Home", "/"],
    ]) {
      await nav.getByRole("link", { name, exact: true }).click();
      await expect(page).toHaveURL(new RegExp(`${path}$`));
      await expect(
        nav.getByRole("link", { name, exact: true }),
      ).toHaveAttribute("aria-current", "page");
      await expect(
        page.getByRole("button", { name: "Open account menu" }),
      ).toBeVisible();
    }
    await page.getByRole("tab", { name: "Following", exact: true }).click();
    await expect(
      nav.getByRole("link", { name: "Home", exact: true }),
    ).toHaveAttribute("aria-current", "page");
  });

  test("account menu preserves access to settings, moderation, profile and theme", async ({
    page,
  }) => {
    await openApp(page);
    const trigger = page.getByRole("button", { name: "Open account menu" });
    const dialog = page.getByRole("dialog", { name: "Account menu" });
    await trigger.click();
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText("@nav_test", { exact: true })).toBeVisible();
    // The page behind the native modal is inert; Escape restores the trigger.
    await page.locator(".composer textarea").evaluate((el) => el.focus());
    expect(
      await dialog.evaluate((el) => el.contains(document.activeElement)),
    ).toBe(true);
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(trigger).toBeFocused();
    await trigger.click();
    await dialog.getByRole("button", { name: /Appearance/ }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await dialog.getByRole("link", { name: "Settings", exact: true }).click();
    await expect(page).toHaveURL(/\/settings$/);
    await expect(dialog).toBeHidden();
    await trigger.click();
    await dialog.getByRole("link", { name: "Moderation", exact: true }).click();
    await expect(page).toHaveURL(/\/moderation$/);
    await trigger.click();
    await dialog.getByRole("link", { name: "My profile", exact: true }).click();
    await expect(page).toHaveURL(/\/nav_test$/);
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  });

  test("regular accounts have no moderation entry and can log out", async ({
    page,
  }) => {
    await openApp(page, false);
    await page.getByRole("button", { name: "Open account menu" }).click();
    const dialog = page.getByRole("dialog", { name: "Account menu" });
    await expect(dialog.getByRole("link", { name: "Moderation" })).toHaveCount(
      0,
    );
    const logout = page.waitForRequest(
      (request) =>
        request.url().endsWith("/auth/logout") && request.method() === "POST",
    );
    await dialog.getByRole("button", { name: "Log out" }).click();
    await logout;
    await expect(page.locator(".auth-page")).toBeVisible();
    expect(await page.evaluate(() => document.body.style.overflow)).not.toBe(
      "hidden",
    );
  });

  test("hides navigation during text entry and keeps the chat composer clear", async ({
    page,
  }) => {
    await openApp(page);
    await page.getByRole("textbox", { name: "Tweet content" }).focus();
    await expect(page.locator(".rail")).toBeHidden();
    await page.getByRole("tab", { name: "For you", exact: true }).click();
    await expect(page.locator(".rail")).toBeVisible();
    await page.goto("/messages/friend");
    const input = page.getByPlaceholder("Message @friend");
    await expect(input).toBeVisible();
    const composer = page.locator(".chat-composer-holder");
    const box = await composer.boundingBox();
    const rail = await page.locator(".rail").boundingBox();
    expect(box!.y + box!.height).toBeLessThanOrEqual(rail!.y);
    await input.focus();
    await expect(page.locator(".rail")).toBeHidden();
    await expect(composer).toHaveCSS("bottom", "0px");
    await input.blur();
    await expect(page.locator(".rail")).toBeVisible();
  });

  test("dismisses the menu on backdrop click and when changing to desktop", async ({
    page,
  }) => {
    await openApp(page);
    const trigger = page.getByRole("button", { name: "Open account menu" });
    const dialog = page.getByRole("dialog", { name: "Account menu" });
    await trigger.click();
    await page.mouse.click(390, 780);
    await expect(dialog).toBeHidden();
    await trigger.click();
    await page.setViewportSize({ width: 1200, height: 800 });
    await expect(dialog).toBeHidden();
    await expect(trigger).toBeHidden();
    await expect(
      page.getByRole("navigation", { name: "Primary" }).getByRole("link"),
    ).toHaveCount(6);
    expect(await page.evaluate(() => document.body.style.overflow)).not.toBe(
      "hidden",
    );
  });
});

test("desktop keeps its full sidebar while composing", async ({
  page,
  isMobile,
}) => {
  test.skip(isMobile, "Desktop layout");
  await openApp(page);
  await expect(
    page.getByRole("navigation", { name: "Primary" }).getByRole("link"),
  ).toHaveCount(6);
  await expect(
    page.getByRole("button", { name: "Open account menu" }),
  ).toBeHidden();
  await page.getByRole("textbox", { name: "Tweet content" }).focus();
  await expect(page.locator(".rail")).toBeVisible();
  await expect(page.locator(".rail")).toHaveCSS("position", "sticky");
});
