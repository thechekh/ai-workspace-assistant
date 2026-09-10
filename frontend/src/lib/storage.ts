/**
 * localStorage / sessionStorage behind try/catch.
 *
 * Both can throw on *access*, not only on write: Safari's private mode, a
 * browser with site data blocked, or a sandboxed iframe raise a SecurityError
 * from `window.localStorage` itself. Everything persisted here (dev mode, the
 * chosen backend, the session id, the token) is a convenience, so the app must
 * run without it rather than fail to start.
 */
export type StorageArea = "local" | "session";

function area(name: StorageArea): Storage {
  return name === "local" ? window.localStorage : window.sessionStorage;
}

export function readStorage(name: StorageArea, key: string): string | null {
  try {
    return area(name).getItem(key);
  } catch {
    return null;
  }
}

/** `null` removes the key. */
export function writeStorage(name: StorageArea, key: string, value: string | null): void {
  try {
    const store = area(name);
    if (value === null) store.removeItem(key);
    else store.setItem(key, value);
  } catch {
    /* no persistence available — nothing to do */
  }
}
