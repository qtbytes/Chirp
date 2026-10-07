import { expect, test, type Locator, type Page } from "@playwright/test";
import { openComposer } from "./composer-helpers";

const transcript =
  "语音输入会一次写入很长的中文句子，图片应当始终排列在完整文字的下方。".repeat(
    6,
  );
const png = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
);

test.beforeEach(async ({ page }) => {
  let upload = 0;
  // Exercise the real composer without a backend or writes to a user account.
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) {
      await route.fulfill({
        json: {
          id: 1,
          username: "composer_test",
          display_name: "Composer test",
          created_at: "2026-01-01T00:00:00Z",
          avatar_url: null,
        },
      });
    } else if (path.includes("/timeline/")) {
      await route.fulfill({ json: { items: [], next_cursor: null } });
    } else if (path.endsWith("/unread-count")) {
      await route.fulfill({ json: { count: 0 } });
    } else if (path.endsWith("/media") && route.request().method() === "POST") {
      await route.fulfill({
        json: { url: `/uploads/composer-test-${++upload}.png` },
      });
    } else if (path.endsWith("/stream")) {
      await route.abort();
    } else if (route.request().method() === "GET") {
      await route.fulfill({ json: [] });
    } else {
      throw new Error(
        `Unexpected API write: ${route.request().method()} ${path}`,
      );
    }
  });
  await page.route("**/uploads/composer-test-*.png", (route) =>
    route.fulfill({ contentType: "image/png", body: png }),
  );
  await page.goto("/");
  await openComposer(page);
  await expect(
    page.getByRole("textbox", { name: "Tweet content" }),
  ).toBeVisible();
});

async function attachImages(page: Page) {
  await page
    .locator(".composer input[type=file]")
    .setInputFiles(
      ["first.png", "second.png"].map((name) => ({
        name,
        mimeType: "image/png",
        buffer: png,
      })),
    );
  await expect(page.locator(".composer-media img")).toHaveCount(2);
}

async function height(field: Locator) {
  return field.evaluate((el) => el.getBoundingClientRect().height);
}

async function expectDraftVisible(page: Page, text: string) {
  await expect(
    page.getByRole("textbox", { name: "Tweet content" }),
  ).toHaveValue(text);
  await expect
    .poll(() =>
      page.locator(".composer-input").evaluate((el) => {
        const field = el.querySelector("textarea")!;
        const highlight = el.querySelector(".composer-highlight")!;
        const range = document.createRange();
        range.selectNodeContents(highlight);
        const lastLine = Array.from(range.getClientRects()).at(-1);
        const media = el.parentElement!.querySelector(".composer-media");
        return (
          field.scrollHeight <= field.clientHeight &&
          (!lastLine ||
            lastLine.bottom <= highlight.getBoundingClientRect().bottom) &&
          (!media ||
            media.getBoundingClientRect().top >=
              el.getBoundingClientRect().bottom)
        );
      }),
    )
    .toBe(true);
}

test("grows during native IME transcription and shrinks after commit", async ({
  page,
  context,
}) => {
  await attachImages(page);
  const field = page.getByRole("textbox", { name: "Tweet content" });
  await field.focus();
  const ime = await context.newCDPSession(page);
  await ime.send("Input.imeSetComposition", {
    text: transcript,
    selectionStart: transcript.length,
    selectionEnd: transcript.length,
  });
  await expectDraftVisible(page, transcript);
  // A narrower viewport must also remeasure while composition is active.
  await page.setViewportSize({ width: 320, height: 800 });
  await expectDraftVisible(page, transcript);
  const expanded = await height(field);

  // Recognition can revise a long interim result down to a few words.
  await ime.send("Input.imeSetComposition", {
    text: "修正",
    selectionStart: 2,
    selectionEnd: 2,
  });
  await expectDraftVisible(page, "修正");
  expect(await height(field)).toBe(expanded);
  await ime.send("Input.insertText", { text: "修正" });
  await expectDraftVisible(page, "修正");
  await expect.poll(() => height(field)).toBeLessThan(expanded);
});

for (const recovery of ["input", "blur"] as const) {
  test(`recovers from a missing compositionend on ${recovery}`, async ({
    page,
  }) => {
    await attachImages(page);
    const field = page.getByRole("textbox", { name: "Tweet content" });
    await field.focus();
    // Explicitly model a voice IME that never sends compositionend.
    await field.evaluate((el, text) => {
      el.dispatchEvent(
        new CompositionEvent("compositionstart", { bubbles: true }),
      );
      Object.getOwnPropertyDescriptor(
        HTMLTextAreaElement.prototype,
        "value",
      )!.set!.call(el, text);
      el.dispatchEvent(
        new InputEvent("input", {
          bubbles: true,
          data: text,
          inputType: "insertCompositionText",
          isComposing: true,
        }),
      );
    }, transcript);
    await expectDraftVisible(page, transcript);
    const expanded = await height(field);

    await field.evaluate((el, recovery) => {
      Object.getOwnPropertyDescriptor(
        HTMLTextAreaElement.prototype,
        "value",
      )!.set!.call(el, "结束");
      el.dispatchEvent(
        new InputEvent("input", {
          bubbles: true,
          data: "结束",
          inputType: "insertText",
          isComposing: recovery === "blur",
        }),
      );
      if (recovery === "blur") (el as HTMLTextAreaElement).blur();
    }, recovery);
    await expectDraftVisible(page, "结束");
    await expect.poll(() => height(field)).toBeLessThan(expanded);
  });
}

test("keeps ordinary long input visible after attaching images and rewrapping", async ({
  page,
}) => {
  const field = page.getByRole("textbox", { name: "Tweet content" });
  await field.fill(transcript);
  await expectDraftVisible(page, transcript);
  await attachImages(page);
  await expectDraftVisible(page, transcript);
  await page.setViewportSize({ width: 360, height: 800 });
  await expectDraftVisible(page, transcript);
  await field.fill("短句");
  await expectDraftVisible(page, "短句");
});
