import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// The built bundle is served by FastAPI: index.html at "/", assets under "/static/".
// In dev, Vite serves at "/" and proxies API calls to the FastAPI process.
export default defineConfig(({ command }) => ({
  plugins: [svelte()],
  base: command === "build" ? "/static/" : "/",
  build: { outDir: "dist" },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:8080",
      "/healthz": "http://127.0.0.1:8080",
    },
  },
}));
