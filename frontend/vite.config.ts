import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Dev server proxies /api to the FastAPI backend so the browser makes same-origin calls.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      // Explicit IPv4 — Node resolves "localhost" to ::1 first, but uvicorn binds
      // 127.0.0.1 by default, which would make proxied /api calls fail.
      // Backend runs on :8077 (port 8000 is often in use on this machine).
      "/api": {
        target: process.env.VITE_API_TARGET ?? "http://127.0.0.1:8077",
        changeOrigin: true,
      },
    },
  },
});
