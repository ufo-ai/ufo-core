import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { PressRow } from "@/components/ui/pressrow";

const TITLE = "GLM-3.5-Flash vs Muse Spark API Pricing #ufo-testing";
const AN_HOUR_AGO = new Date(Date.now() - 3_600_000).toISOString();
const A_WEEK_BACK = "2026-08-07T09:30:00Z";

/** jsdom lays nothing out, so every width reads 0 and no line is ever cut. These stub the two a
 *  travelling line measures: the words' own width, and the measure the row leaves them. */
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

  await waitFor(() => expect(words.getAttribute("style")).toContain("translateX(-416px)"));
  expect(words.parentElement?.className).toContain("line-fade-x");
});

test("the line comes back to its first word when the pointer leaves", async () => {
  laidOut(600, 200);
  render(<PressRow line={TITLE} onPress={() => {}} />);
  const row = screen.getByRole("button");
  const words = screen.getByText(TITLE);

  await userEvent.hover(row);
  await waitFor(() => expect(words.getAttribute("style")).toContain("translateX(-416px)"));
  await userEvent.unhover(row);

  await waitFor(() => expect(words.getAttribute("style")).toContain("translateX(0px)"));
  expect(words.parentElement?.className).toContain("line-fade-e");
});

test("a line the row holds whole stands still, so a list is not set moving by a pointer crossing it", async () => {
  laidOut(200, 200);
  render(<PressRow line={TITLE} onPress={() => {}} />);

  await userEvent.hover(screen.getByRole("button"));

  const words = screen.getByText(TITLE);
  expect(words.getAttribute("style")).toContain("translateX(0px)");
  expect(words.parentElement?.className).not.toContain("line-fade");
});

test("the keyboard reaches the tail the pointer does", async () => {
  laidOut(600, 200);
  render(<PressRow line={TITLE} onPress={() => {}} />);

  await userEvent.tab();

  await waitFor(() =>
    expect(screen.getByText(TITLE).getAttribute("style")).toContain("translateX(-416px)"),
  );
});

test("the trailing note travels with the line, as the one thing the row says", async () => {
  laidOut(600, 200);
  render(<PressRow line={TITLE} note="Slack" onPress={() => {}} />);

  await userEvent.hover(screen.getByRole("button"));

  await waitFor(() =>
    expect(screen.getByText(TITLE + " Slack").getAttribute("style")).toContain("translateX(-416px)"),
  );
});

test("a recent row states its distance from now in one number and one letter", () => {
  render(<PressRow line={TITLE} when={AN_HOUR_AGO} onPress={() => {}} />);

  expect(screen.getByRole("button").textContent).toContain("1h");
  expect(screen.getByRole("button").textContent).not.toContain("ago");
});

test("an older row keeps the same scale, and carries the whole stamp with it", () => {
  render(<PressRow line={TITLE} when={A_WEEK_BACK} onPress={() => {}} />);

  const stamp = screen.getByRole("button").querySelector("time");
  expect(stamp?.textContent).toMatch(/^\d+d$/);
  expect(stamp?.getAttribute("datetime")).toBe(A_WEEK_BACK);
  expect(stamp?.getAttribute("title")).toBe("Aug 7 2026 at 3:00 PM GMT+5:30");
});

test("the stamp is drawn at rest, not withheld until the pointer arrives", () => {
  render(<PressRow line={TITLE} when={AN_HOUR_AGO} onPress={() => {}} />);

  const stamp = screen.getByRole("button").querySelector("time");
  expect(stamp?.parentElement?.className).not.toContain("opacity-0");
});
