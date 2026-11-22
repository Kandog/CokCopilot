import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, the Python backend runs with `python -m cokcopilot --server` (port 8765)
// and COKCOPILOT_DEV=1 so it accepts requests proxied from this dev server.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8765", changeOrigin: true } },
  },
  build: { outDir: "dist", chunkSizeWarningLimit: 1500 },
});
