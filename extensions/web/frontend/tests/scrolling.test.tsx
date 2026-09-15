import { act, cleanup, render } from "@testing-library/react";
import { beforeEach, expect, onTestFinished, test, vi } from "vitest";

import { App } from "@/App";
import { SCROLL_MARK, SCROLL_QUIET_MS } from "@/views/shell";

import { AGENT, MEMBER, useStreamFake, wire } from "./harness";

/** Time is faked before the mount so the quiet period is driven rather than waited on. */
function shell() {
  vi.useFakeTimers();
  onTestFinished(() => {
    vi.useRealTimers();
  });
  useStreamFake();
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  const pane = document.createElement("div");
  pane.tabIndex = 0;
  document.body.append(pane);
  onTestFinished(() => pane.remove());
  return pane;
}

const scroll = (pane: Element) => pane.dispatchEvent(new Event("scroll"));

const wait = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));

beforeEach(() => {
  location.hash = "";
});

test("a pane wears the scroll mark while it moves and loses it once it stops", async () => {
  const pane = shell();

  expect(pane.hasAttribute(SCROLL_MARK)).toBe(false);
  scroll(pane);
  expect(pane.hasAttribute(SCROLL_MARK)).toBe(true);

  await wait(SCROLL_QUIET_MS - 1);
  expect(pane.hasAttribute(SCROLL_MARK)).toBe(true);
  scroll(pane);
  await wait(SCROLL_QUIET_MS - 1);
  expect(pane.hasAttribute(SCROLL_MARK)).toBe(true);

  await wait(1);
  expect(pane.hasAttribute(SCROLL_MARK)).toBe(false);
});

test("a pointer resting over a pane and a caret inside one leave the thumb hushed", async () => {
  const pane = shell();

  pane.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
  pane.dispatchEvent(new MouseEvent("mousemove", { bubbles: true }));
  pane.focus();
  await wait(0);

  expect(document.activeElement).toBe(pane);
  expect(pane.hasAttribute(SCROLL_MARK)).toBe(false);
});

test("a gesture writes the mark once and holds one timer, whatever its frame rate", () => {
  const pane = shell();
  const held = vi.getTimerCount();
  const wrote = vi.spyOn(pane, "setAttribute");

  for (let frame = 0; frame < 60; frame += 1) scroll(pane);

  expect(wrote).toHaveBeenCalledTimes(1);
  expect(vi.getTimerCount()).toBe(held + 1);
});

test("each pane keeps its own bar, so one still moving is not hushed by one that stopped", async () => {
  const first = shell();
  const second = document.createElement("div");
  document.body.append(second);
  onTestFinished(() => second.remove());

  scroll(first);
  await wait(SCROLL_QUIET_MS / 2);
  scroll(second);
  await wait(SCROLL_QUIET_MS / 2);

  expect(first.hasAttribute(SCROLL_MARK)).toBe(false);
  expect(second.hasAttribute(SCROLL_MARK)).toBe(true);
});

test("the shell takes its listener and every mark it wrote away with it", async () => {
  const pane = shell();
  scroll(pane);
  expect(pane.hasAttribute(SCROLL_MARK)).toBe(true);

  await act(async () => cleanup());

  expect(pane.hasAttribute(SCROLL_MARK)).toBe(false);
  scroll(pane);
  expect(pane.hasAttribute(SCROLL_MARK)).toBe(false);
});
