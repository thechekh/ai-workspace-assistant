import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [vue()],
  server: {
    // Dev mode: Vite on :5173, FastAPI on :8000. Everything the app calls at
    // runtime must be proxied — the store makes 7 fetches to /api/* (info,
    // health, documents, audit trail, reindex), so without this rule
    // `npm run dev` 404s them all.
    proxy: {
      "/chat": { target: "ws://localhost:8000", ws: true },
      "/api": "http://localhost:8000",
    },
  },
});
