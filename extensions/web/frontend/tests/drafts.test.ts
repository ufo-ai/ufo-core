import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { clearDraft, flushDrafts, installDraftFlush, readDraft, writeDraft } from "@/lib/drafts";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

test("a draft lands in storage only after the debounce settles", () => {
  writeDraft("m1/chat-1", "half a tho");
  expect(readDraft("m1/chat-1")).toBe("");
  vi.advanceTimersByTime(400);
  expect(readDraft("m1/chat-1")).toBe("half a tho");
});

test("rapid typing keeps only the newest draft", () => {
  writeDraft("m1/chat-1", "first");
  vi.advanceTimersByTime(200);
  writeDraft("m1/chat-1", "first and second");
  vi.advanceTimersByTime(399);
  expect(readDraft("m1/chat-1")).toBe("");
  vi.advanceTimersByTime(1);
  expect(readDraft("m1/chat-1")).toBe("first and second");
});

test("typing in a second chat within the debounce loses neither draft", () => {
  writeDraft("m1/chat-1", "for the first");
  writeDraft("m1/chat-2", "for the second");
  vi.advanceTimersByTime(400);
  expect(readDraft("m1/chat-1")).toBe("for the first");
  expect(readDraft("m1/chat-2")).toBe("for the second");
});

test("hiding the page flushes pending drafts immediately", () => {
  const uninstall = installDraftFlush();
  writeDraft("m1/chat-1", "about to close the tab");
  window.dispatchEvent(new Event("pagehide"));
  expect(readDraft("m1/chat-1")).toBe("about to close the tab");
  uninstall();
});

test("backgrounding the tab flushes pending drafts", () => {
  const uninstall = installDraftFlush();
  writeDraft("m1/chat-1", "switched apps");
  const descriptor = Object.getOwnPropertyDescriptor(Document.prototype, "visibilityState");
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
  document.dispatchEvent(new Event("visibilitychange"));
  if (descriptor) Object.defineProperty(Document.prototype, "visibilityState", descriptor);
  else Reflect.deleteProperty(document, "visibilityState");
  expect(readDraft("m1/chat-1")).toBe("switched apps");
  uninstall();
});

test("uninstalling the flush hooks flushes what is pending", () => {
  const uninstall = installDraftFlush();
  writeDraft("m1/chat-1", "unmounting now");
  uninstall();
  expect(readDraft("m1/chat-1")).toBe("unmounting now");
});

test("clearing a draft cancels the pending write and empties storage", () => {
  writeDraft("m1/chat-1", "sent already");
  clearDraft("m1/chat-1");
  vi.advanceTimersByTime(400);
  expect(readDraft("m1/chat-1")).toBe("");
});

test("an emptied composer clears its stored draft on flush", () => {
  writeDraft("m1/chat-1", "something");
  flushDrafts();
  writeDraft("m1/chat-1", "");
  flushDrafts();
  expect(readDraft("m1/chat-1")).toBe("");
});

test("a draft is capped rather than stored unbounded", () => {
  writeDraft("m1/chat-1", "y".repeat(30_000));
  flushDrafts();
  expect(readDraft("m1/chat-1").length).toBe(20_000);
});
