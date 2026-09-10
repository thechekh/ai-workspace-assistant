import { computed, onBeforeUnmount, ref, watch, type Ref } from "vue";

/** Which header popover is open — one at a time across the app. */
const active = ref<string | null>(null);

/**
 * A header popover (Chats, Documents): exclusive, closed by Escape or a
 * pointer press outside `root`, and handing focus back to `trigger` when
 * closed from the keyboard or its own controls — so a keyboard user is not
 * dropped at <body>. `root` should contain the trigger as well as the panel,
 * otherwise pressing the trigger counts as "outside" and reopens what it just
 * closed.
 */
export function usePopover(
  name: string,
  root: Ref<HTMLElement | null>,
  trigger: Ref<HTMLElement | null>,
) {
  const open = computed(() => active.value === name);

  function show(): void {
    active.value = name;
  }

  function close(options: { focusTrigger?: boolean } = {}): void {
    if (active.value !== name) return;
    active.value = null;
    if (options.focusTrigger) trigger.value?.focus();
  }

  function toggle(): void {
    if (open.value) close({ focusTrigger: true });
    else show();
  }

  function onKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape") close({ focusTrigger: true });
  }

  function onPointerDown(event: Event): void {
    const target = event.target;
    if (target instanceof Node && root.value?.contains(target)) return;
    close();
  }

  function unlisten(): void {
    document.removeEventListener("keydown", onKeydown);
    document.removeEventListener("pointerdown", onPointerDown);
  }

  watch(
    open,
    (isOpen) => {
      if (isOpen) {
        document.addEventListener("keydown", onKeydown);
        document.addEventListener("pointerdown", onPointerDown);
      } else {
        unlisten();
      }
    },
    { immediate: true },
  );

  onBeforeUnmount(() => {
    unlisten();
    close();
  });

  return { open, toggle, close };
}
