// vue-tsc resolves .vue modules itself; this wildcard only serves plain tsc —
// which is what typescript-eslint's type-aware rules run on — so a component
// import is a typed component there instead of an implicit `any`.
declare module "*.vue" {
  import type { DefineComponent } from "vue";

  const component: DefineComponent<object, object, unknown>;
  export default component;
}
