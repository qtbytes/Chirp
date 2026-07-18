import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // The emoji dataset is deliberately NOT code-split (a lazy panel made
        // the first open visibly laggy), which pushes the single bundle past
        // Rollup's 500 kB warning. Splitting the framework out instead keeps
        // both chunks under it — they load in parallel up front, so nothing
        // gets slower, and the vendor chunk only changes when dependencies
        // do, so it stays browser-cached across app deploys.
        manualChunks: {
          vendor: ["react", "react-dom", "react-router-dom"],
        },
      },
    },
  },
});
