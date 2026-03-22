import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";
import { copyFileSync } from "fs";

// Copy manifest.json to dist after build
function copyManifest() {
  return {
    name: "copy-manifest",
    closeBundle() {
      copyFileSync(
        resolve(__dirname, "manifest.json"),
        resolve(__dirname, "dist/manifest.json")
      );
    },
  };
}

export default defineConfig({
  plugins: [react(), copyManifest()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        popup: resolve(__dirname, "popup.html"),
        service_worker: resolve(__dirname, "src/background/service_worker.ts"),
        scanner: resolve(__dirname, "src/content/scanner.ts"),
      },
      output: {
        entryFileNames: (chunkInfo) => {
          if (chunkInfo.name === "service_worker") {
            return "src/background/service_worker.js";
          }
          if (chunkInfo.name === "scanner") {
            return "src/content/scanner.js";
          }
          return "assets/[name]-[hash].js";
        },
      },
    },
  },
});
