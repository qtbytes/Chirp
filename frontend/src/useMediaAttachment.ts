import { ChangeEvent, useRef, useState } from "react";
import { ApiError, uploadMedia } from "./api";

const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const MAX_VIDEO_BYTES = 50 * 1024 * 1024;
export const MAX_MEDIA_ITEMS = 4;
export const ACCEPTED_MEDIA =
  "image/jpeg,image/png,image/webp,image/gif,video/mp4,video/webm,video/quicktime";

function messageFrom(err: unknown): string {
  if (err instanceof ApiError || err instanceof Error) {
    return err.message;
  }
  return "Upload failed.";
}

// Manages up to MAX_MEDIA_ITEMS image attachments for a composer: multi-file
// selection, concurrent uploads, per-item removal, and reset. The returned
// object is passed to <MediaButton> and <MediaPreview>.
export function useMediaAttachment() {
  const [mediaUrls, setMediaUrls] = useState<string[]>([]);
  const [uploadingCount, setUploadingCount] = useState(0);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  async function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    // Reset so selecting the same file again still fires onChange.
    event.target.value = "";
    if (files.length === 0) {
      return;
    }

    setError("");
    // Reserve slots against what's already attached plus in-flight uploads.
    let remaining = MAX_MEDIA_ITEMS - mediaUrls.length - uploadingCount;
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
          setMediaUrls((prev) => (prev.length < MAX_MEDIA_ITEMS ? [...prev, url] : prev));
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
    setMediaUrls((prev) => prev.filter((item) => item !== url));
  }

  function clear() {
    setMediaUrls([]);
    setError("");
  }

  const uploading = uploadingCount > 0;
  const atLimit = mediaUrls.length + uploadingCount >= MAX_MEDIA_ITEMS;

  return { mediaUrls, uploading, atLimit, error, inputRef, onFileChange, openPicker, remove, clear };
}

export type MediaAttachment = ReturnType<typeof useMediaAttachment>;
