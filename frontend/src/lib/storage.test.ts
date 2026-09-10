import { afterEach, describe, expect, it, vi } from "vitest";

import { readStorage, writeStorage } from "./storage";

describe("storage helpers", () => {
  const realLocal = globalThis.localStorage;

  afterEach(() => {
    vi.stubGlobal("localStorage", realLocal);
    localStorage.clear();
    sessionStorage.clear();
  });

  it("round-trips values and removes them with null", () => {
    writeStorage("local", "k", "v");
    expect(readStorage("local", "k")).toBe("v");
    expect(localStorage.getItem("k")).toBe("v");

    writeStorage("local", "k", null);
    expect(readStorage("local", "k")).toBeNull();
  });

  it("keeps the two areas apart", () => {
    writeStorage("session", "k", "s");
    expect(readStorage("local", "k")).toBeNull();
    expect(readStorage("session", "k")).toBe("s");
  });

  it("neither throws nor persists when storage access is blocked", () => {
    vi.stubGlobal(
      "localStorage",
      new Proxy(
        {},
        {
          get() {
            throw new DOMException("blocked", "SecurityError");
          },
        },
      ),
    );
    expect(() => writeStorage("local", "k", "v")).not.toThrow();
    expect(readStorage("local", "k")).toBeNull();
  });
});
