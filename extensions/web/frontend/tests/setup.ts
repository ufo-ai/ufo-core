import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

import { resetChatStore } from "@/lib/chatStore";
import { resetStreams } from "@/lib/turnStream";

const KEY_FAULT = /unique "key" prop|two children with the same key/;
let keyFaults: string[] = [];

function memoryStorage(): Pick<Storage, "getItem" | "setItem" | "removeItem" | "clear"> {
  const backing = new Map<string, string>();
  return {
    getItem: (key) => backing.get(key) ?? null,
    setItem: (key, value) => void backing.set(key, String(value)),
    removeItem: (key) => void backing.delete(key),
    clear: () => backing.clear(),
  };
}

function installWhatJsdomLacks() {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => {};
  Element.prototype.releasePointerCapture = () => {};
  Element.prototype.scrollIntoView = () => {};
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  globalThis.IntersectionObserver = class {
    constructor(private callback: IntersectionObserverCallback) {}
    observe(target: Element) {
      this.callback(
        [{ isIntersecting: true, target } as IntersectionObserverEntry],
        this as unknown as IntersectionObserver,
      );
    }
    unobserve() {}
    disconnect() {}
  } as unknown as typeof IntersectionObserver;
  globalThis.matchMedia = (media: string) => ({
    media,
    matches: false,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  });
}

if (typeof document !== "undefined") installWhatJsdomLacks();

beforeEach(() => {
  keyFaults = [];
  vi.stubGlobal("localStorage", memoryStorage());
  const report = console.error;
  vi.spyOn(console, "error").mockImplementation((...args: unknown[]) => {
    if (KEY_FAULT.test(String(args[0]))) keyFaults.push(String(args[0]).split("\n")[0]);
    else report(...args);
  });
});

afterEach(() => {
  const faults = keyFaults;
  keyFaults = [];
  resetChatStore();
  resetStreams();
  if (typeof localStorage !== "undefined") localStorage.clear();
  vi.unstubAllGlobals();
  if (typeof document !== "undefined") {
    cleanup();
    history.replaceState(null, "", location.pathname);
  }
  if (faults.length) throw new Error(faults[0]);
});
