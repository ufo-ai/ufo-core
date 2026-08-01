import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

import { resetChatStore } from "@/lib/chatStore";

const KEY_FAULT = /unique "key" prop|two children with the same key/;
let keyFaults: string[] = [];

beforeEach(() => {
  keyFaults = [];
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
  vi.unstubAllGlobals();
  if (typeof document !== "undefined") {
    cleanup();
    location.hash = "";
  }
  if (faults.length) throw new Error(faults[0]);
});
