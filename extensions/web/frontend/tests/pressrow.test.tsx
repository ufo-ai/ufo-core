import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { PressRow } from "@/components/ui/pressrow";

const TITLE = "GLM-3.5-Flash vs Muse Spark API Pricing #ufo-testing";
const AN_HOUR_AGO = new Date(Date.now() - 3_600_000).toISOString();
const A_WEEK_BACK = "2026-08-07T09:30:00Z";

/** jsdom lays nothing out, so every width reads 0 and no line is ever cut. These stub the two a
 *  travelling line measures — the words' own width, and the measure the row leaves them — which is
 *  the whole input to how far it goes. */
function laidOut(words: number, measure: number): void {
  vi.spyOn(Element.prototype, "clientWidth", "get").mockReturnValue(measure);
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
    width: words,
    height: 0,
    top: 0,
    left: 0,
    right: words,
    bottom: 0,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  } as DOMRect);
}

afterEach(() => {
  vi.restoreAllMocks();
});

test("a line too long for the row travels out to its tail while the pointer is on it", async () => {
  laidOut(600, 200);
  render(<PressRow line={TITLE} when={A_WEEK_BACK} onPress={() => {}} />);
  const words = screen.getByText(TITLE);
  expect(words.getAttribute("style")).toContain("translateX(0px)");

  await userEvent.hover(screen.getByRole("button"));

  await waitFor(() => expect(words.getAttribute("style")).toContain("translateX(-400px)"));
  // The ellipsis goes with it: a mark saying there is more has nothing to say while the more is
  // being read.
  expect(words.parentElement?.className).toContain("text-clip");
});

test("the line comes back to its first word when the pointer leaves", async () => {
  laidOut(600, 200);
  render(<PressRow line={TITLE} onPress={() => {}} />);
  const row = screen.getByRole("button");
  const words = screen.getByText(TITLE);

  await userEvent.hover(row);
  await waitFor(() => expect(words.getAttribute("style")).toContain("translateX(-400px)"));
  await userEvent.unhover(row);

  await waitFor(() => expect(words.getAttribute("style")).toContain("translateX(0px)"));
  expect(words.parentElement?.className).toContain("text-ellipsis");
});

test("a line the row holds whole stands still, so a list is not set moving by a pointer crossing it", async () => {
  laidOut(200, 200);
  render(<PressRow line={TITLE} onPress={() => {}} />);

  await userEvent.hover(screen.getByRole("button"));

  const words = screen.getByText(TITLE);
  expect(words.getAttribute("style")).toContain("translateX(0px)");
  expect(words.parentElement?.className).toContain("text-ellipsis");
});

test("the keyboard reaches the tail the pointer does", async () => {
  laidOut(600, 200);
  render(<PressRow line={TITLE} onPress={() => {}} />);

  await userEvent.tab();

  await waitFor(() =>
    expect(screen.getByText(TITLE).getAttribute("style")).toContain("translateX(-400px)"),
  );
});

test("the trailing note travels with the line, as the one thing the row says", async () => {
  laidOut(600, 200);
  render(<PressRow line={TITLE} note="Slack" onPress={() => {}} />);

  await userEvent.hover(screen.getByRole("button"));

  await waitFor(() =>
    expect(screen.getByText(TITLE + " Slack").getAttribute("style")).toContain("translateX(-400px)"),
  );
});

/** The stamp is the distance from now while that is what says a row is recent, and the day itself
 *  once the day is what matters — a few characters either way, so the words the row came to say
 *  keep the width a full date would take. */
test("a recent row states how long ago it moved, not the date", () => {
  render(<PressRow line={TITLE} when={AN_HOUR_AGO} onPress={() => {}} />);

  expect(screen.getByRole("button").textContent).toContain("1h ago");
});

test("an older row states the day it moved, and carries the whole stamp with it", () => {
  render(<PressRow line={TITLE} when={A_WEEK_BACK} onPress={() => {}} />);

  const stamp = screen.getByText("Aug 7 2026");
  expect(stamp.getAttribute("datetime")).toBe(A_WEEK_BACK);
  expect(stamp.getAttribute("title")).toBe("Aug 7 2026 at 09:30 UTC");
});
