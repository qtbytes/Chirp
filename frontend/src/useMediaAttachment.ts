import { ChangeEvent, useRef, useState } from "react";
import { ApiError, uploadMedia } from "./api";

const MAX_MEDIA_BYTES = 5 * 1024 * 1024;
export const ACCEPTED_MEDIA = "image/jpeg,image/png,image/webp,image/gif";

function messageFrom(err: unknown): string {
  if (err instanceof ApiError || err instanceof Error) {
    return err.message;
  }
  return "Upload failed.";
}

// Manages a single optional image attachment for a composer: file selection,
// upload, the resulting media_url, and remove/reset. Returned object is passed
// to <MediaButton> and <MediaPreview>.
export function useMediaAttachment() {
  const [mediaUrl, setMediaUrl] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  async function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    // Reset so selecting the same file again still fires onChange.
    event.target.value = "";
    if (!file) {
      return;
    }
    if (file.size > MAX_MEDIA_BYTES) {
      setError("Image must be 5 MB or smaller.");
      return;
    }

    setError("");
    setUploading(true);
    try {
      const { url } = await uploadMedia(file);
      setMediaUrl(url);
    } catch (err) {
      setError(messageFrom(err));
    } finally {
      setUploading(false);
    }
  }

  function openPicker() {
    inputRef.current?.click();
  }

  function clear() {
    setMediaUrl(null);
    setError("");
  }

  return { mediaUrl, uploading, error, inputRef, onFileChange, openPicker, clear };
}

export type MediaAttachment = ReturnType<typeof useMediaAttachment>;
