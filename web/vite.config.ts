import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built to static files and served by the same FastAPI app the data comes
// from — one process, one container, which is this system's first constraint.
// `dev` proxies the API instead, so `npm run dev` needs no CORS entry and
// `board_origins` in config.yaml stays empty for the common case.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    proxy: {
      "/api": { target: "http://127.0.0.1:8086", changeOrigin: true },
    },
  },
});
