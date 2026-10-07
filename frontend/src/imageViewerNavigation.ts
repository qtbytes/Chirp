import { useLocation, useNavigate } from "react-router-dom";
import type { LightboxImage } from "./components";

type ImageViewerState = {
  images: LightboxImage[];
  initialIndex: number;
  backgroundKey: string;
};

export function getImageViewer(state: unknown): ImageViewerState | null {
  const viewer = (state as { imageViewer?: ImageViewerState } | null)?.imageViewer;
  if (!viewer || !Array.isArray(viewer.images) || !viewer.images.length ||
      !viewer.images.every((image) => typeof image?.src === "string" && typeof image?.alt === "string") ||
      !Number.isInteger(viewer.initialIndex) || viewer.initialIndex < 0 ||
      viewer.initialIndex >= viewer.images.length || typeof viewer.backgroundKey !== "string") return null;
  return viewer;
}

/** One history entry per viewing session; changing images stays within it. */
export function useOpenImageViewer() {
  const location = useLocation();
  const navigate = useNavigate();
  return (images: LightboxImage[], initialIndex: number) => {
    navigate(location.pathname + location.search + location.hash, {
      state: {
        ...location.state,
        imageViewer: { images, initialIndex, backgroundKey: location.key } satisfies ImageViewerState,
      },
      preventScrollReset: true,
    });
  };
}
