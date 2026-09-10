import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [vue()],
  build: {
    rolldownOptions: {
      output: {
        // Vendor code changes on a dependency bump, app code on every commit:
        // separate chunks keep the vendor half cached across deploys. The
        // Markdown pipeline is the largest slice, so it gets its own. (Vite 8
        // bundles with rolldown, where `manualChunks` is deprecated in favour
        // of these groups; a function-form manualChunks would be rewritten
        // into exactly this.)
        codeSplitting: {
          groups: [
            {
              name: "markdown",
              test: /node_modules[\\/](markdown-it|highlight\.js|linkify-it|mdurl|uc\.micro|entities|punycode\.js)[\\/]/,
              priority: 20,
            },
            { name: "vendor", test: /node_modules[\\/]/, priority: 10 },
          ],
        },
      },
    },
  },
  server: {
    // Dev mode: Vite on :5173, FastAPI on :8000. Everything the app calls at
    // runtime must be proxied — every /api/* request (info, health, sessions
    // with their transcripts and turns, documents) and the /chat socket —
    // otherwise `npm run dev` 404s them all.
    proxy: {
      "/chat": { target: "ws://localhost:8000", ws: true },
      "/api": "http://localhost:8000",
    },
  },
});
