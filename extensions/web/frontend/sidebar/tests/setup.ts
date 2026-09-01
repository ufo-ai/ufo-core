import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

import { resetAppStatusStore } from "@/lib/appStatusStore";
import { resetChatStore } from "@/lib/chatStore";
import { resetRailStore } from "@/lib/railStore";
import { resetRouter } from "@/lib/router";
import { resetScheme } from "@/lib/scheme";
import { resetStreams } from "@/lib/turnStream";

// A CI failure's DOM dump is the one record of what actually rendered there; the default limit
// cuts it off inside the page's own top bar.
process.env.DEBUG_PRINT_LIMIT ??= "30000";

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
  // jsdom's Blob hands its bytes back only through FileReader, so an attached file a browser reads
  // with `arrayBuffer()` throws here. Reading it through the reader keeps the two answering alike.
  Blob.prototype.arrayBuffer ??= function arrayBuffer(this: Blob) {
    return new Promise<ArrayBuffer>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as ArrayBuffer);
      reader.onerror = () => reject(reader.error);
      reader.readAsArrayBuffer(this);
    });
  };
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => {};
  Element.prototype.releasePointerCapture = () => {};
  Element.prototype.scrollIntoView = () => {};
  // jsdom gives an element `scrollTop` but no `scrollTo` to move it, so a transcript that scrolls
  // itself throws where a browser would simply scroll. Writing the offset through keeps the two
  // agreeing, which is what the code under test reads back.
  Element.prototype.scrollTo = function scrollTo(
    to?: number | ScrollToOptions,
    top?: number,
  ): void {
    const asked = typeof to === "object" ? to : { left: to, top };
    if (asked?.top !== undefined) this.scrollTop = asked.top;
    if (asked?.left !== undefined) this.scrollLeft = asked.left;
  };
  // jsdom implements no `window.open`, and calling it there raises rather than returning anything a
  // caller can read. A member's browser may also refuse the window, and that refusal is the case
  // every act already falls back from — so it is the default here, and a test that cares about the
  // window opening stubs this with its own.
  window.open = () => null;
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
    takeRecords() {
      return [];
    }
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
  vi.stubGlobal("sessionStorage", memoryStorage());
  const report = console.error;
  vi.spyOn(console, "error").mockImplementation((...args: unknown[]) => {
    if (KEY_FAULT.test(String(args[0]))) keyFaults.push(String(args[0]).split("\n")[0]);
    else report(...args);
  });
});

afterEach(() => {
  const faults = keyFaults;
  keyFaults = [];
  resetAppStatusStore();
  resetChatStore();
  resetRailStore();
  resetRouter();
  resetScheme();
  resetStreams();
  if (typeof localStorage !== "undefined") localStorage.clear();
  if (typeof sessionStorage !== "undefined") sessionStorage.clear();
  vi.unstubAllGlobals();
  if (typeof document !== "undefined") {
    cleanup();
    history.replaceState(null, "", location.pathname);
  }
  if (faults.length) throw new Error(faults[0]);
});
