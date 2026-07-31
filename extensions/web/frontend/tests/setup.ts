import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

import { resetChatStore } from "@/lib/chatStore";

afterEach(() => {
  resetChatStore();
  vi.unstubAllGlobals();
  if (typeof document === "undefined") return;
  cleanup();
  location.hash = "";
});
