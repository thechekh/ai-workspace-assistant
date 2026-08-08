// Flat config. Mirrors the backend's posture: correctness rules on, and
// formatting delegated entirely to Prettier (eslint-config-prettier last,
// so the two can never disagree about style).
import js from "@eslint/js";
import prettier from "eslint-config-prettier";
import vue from "eslint-plugin-vue";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist/**", "node_modules/**"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...vue.configs["flat/recommended"],
  {
    files: ["**/*.vue"],
    languageOptions: {
      parserOptions: { parser: tseslint.parser },
    },
  },
  {
    files: ["**/*.ts", "**/*.vue"],
    rules: {
      // `no-undef` is redundant under TypeScript and actively wrong here: it
      // does not know the DOM lib, so it flags HTMLElement/DragEvent/FileList
      // as undefined. tsc (via `npm run typecheck`) is the real checker.
      "no-undef": "off",
      // Unused names are an error, but `_`-prefixed ones are a deliberate
      // "I know, I'm ignoring this" (e.g. unused callback args).
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
  {
    // The one v-html in the app renders model output. That is a real XSS
    // surface, which is exactly why markdown-it runs with `html: false` and
    // why src/lib/markdown.test.ts asserts hostile input stays escaped.
    // Acknowledged deliberately here rather than silenced globally.
    files: ["src/components/MarkdownContent.vue"],
    rules: { "vue/no-v-html": "off" },
  },
  prettier,
);
