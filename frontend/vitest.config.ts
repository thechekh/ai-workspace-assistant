import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: "happy-dom",
    include: ["src/**/*.test.ts"],
    setupFiles: ["src/test/setup.ts"],
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,vue}"],
      exclude: ["src/**/*.test.ts", "src/**/*.d.ts", "src/test/**", "src/main.ts"],
      // A ratchet, like the backend's `fail_under`: measured at 83.7% lines
      // on 2026-09-10, set a few points below so an unrelated refactor does
      // not fail the build but deleting tests does. Raise it, never lower it.
      thresholds: { lines: 80, statements: 78, functions: 72, branches: 62 },
    },
  },
});
