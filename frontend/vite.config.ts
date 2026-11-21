import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// The dev server proxies /api to the backend so the browser sees one origin (no CORS needed).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const api = env.VITE_API_PROXY ?? "http://127.0.0.1:8000";
  return {
    plugins: [react()],
    server: { port: 5173, proxy: { "/api": { target: api, changeOrigin: true } } },
    preview: { proxy: { "/api": { target: api, changeOrigin: true } } },
    build: { chunkSizeWarningLimit: 1500 },
  };
});
