import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      // Dev mode: Vite on :5173, FastAPI on :8000
      "/chat": { target: "ws://localhost:8000", ws: true },
      "/healthz": "http://localhost:8000",
    },
  },
});
