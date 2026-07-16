import { ChangeEvent, useRef, useState } from "react";
import { ApiError, uploadMedia } from "./api";

const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const MAX_VIDEO_BYTES = 50 * 1024 * 1024;
export const MAX_MEDIA_ITEMS = 4;
export const MAX_ALT_LENGTH = 2000;
export const ACCEPTED_MEDIA =
  "image/jpeg,image/png,image/webp,image/gif,video/mp4,video/webm,video/quicktime";

export type MediaItem = { url: string; alt: string };

function messageFrom(err: unknown): string {
  if (err instanceof ApiError || err instanceof Error) {
    return err.message;
  }
  return "Upload failed.";
}

// Manages up to MAX_MEDIA_ITEMS image attachments for a composer: multi-file
// selection, concurrent uploads, per-item removal, per-image alt text, and
// reset. The returned object is passed to <MediaButton> and <MediaPreview>.
export function useMediaAttachment(initial: string[] = [], initialAlts: string[] = []) {
  const [items, setItems] = useState<MediaItem[]>(
    initial.map((url, i) => ({ url, alt: initialAlts[i] ?? "" })),
  );
  const [uploadingCount, setUploadingCount] = useState(0);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const mediaUrls = items.map((item) => item.url);
  const mediaAlts = items.map((item) => item.alt);

  async function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    // Reset so selecting the same file again still fires onChange.
    event.target.value = "";
    if (files.length === 0) {
      return;
    }

    setError("");
    // Reserve slots against what's already attached plus in-flight uploads.
    let remaining = MAX_MEDIA_ITEMS - items.length - uploadingCount;
    const toUpload: File[] = [];
    for (const file of files) {
      if (remaining <= 0) {
        setError(`You can attach up to ${MAX_MEDIA_ITEMS} files.`);
        break;
      }
      const isVideo = file.type.startsWith("video/");
      const maxBytes = isVideo ? MAX_VIDEO_BYTES : MAX_IMAGE_BYTES;
      if (file.size > maxBytes) {
        setError(
          isVideo
            ? "Each video must be 50 MB or smaller."
            : "Each image must be 5 MB or smaller.",
        );
        continue;
      }
      toUpload.push(file);
      remaining -= 1;
    }
    if (toUpload.length === 0) {
      return;
    }

    setUploadingCount((count) => count + toUpload.length);
    await Promise.all(
      toUpload.map(async (file) => {
        try {
          const { url } = await uploadMedia(file);
          // Functional update guards the cap across concurrent resolves.
          setItems((prev) =>
            prev.length < MAX_MEDIA_ITEMS ? [...prev, { url, alt: "" }] : prev,
          );
        } catch (err) {
          setError(messageFrom(err));
        } finally {
          setUploadingCount((count) => count - 1);
        }
      }),
    );
  }

  function openPicker() {
    inputRef.current?.click();
  }

  function remove(url: string) {
    setItems((prev) => prev.filter((item) => item.url !== url));
  }

  function setAlt(url: string, alt: string) {
    setItems((prev) =>
      prev.map((item) => (item.url === url ? { ...item, alt } : item)),
    );
  }

  function clear() {
    setItems([]);
    setError("");
  }

  const uploading = uploadingCount > 0;
  const atLimit = items.length + uploadingCount >= MAX_MEDIA_ITEMS;

  return {
    items,
    mediaUrls,
    mediaAlts,
    uploading,
    atLimit,
    error,
    inputRef,
    onFileChange,
    openPicker,
    remove,
    setAlt,
    clear,
  };
}

export type MediaAttachment = ReturnType<typeof useMediaAttachment>;
