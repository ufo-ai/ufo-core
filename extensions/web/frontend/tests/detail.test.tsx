import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { Detail } from "@/components/ui/detail";

test("an aspect is a pair, so a reader that builds pairs out of the markup finds it", () => {
  render(<Detail label="Runs">Every hour, on the hour.</Detail>);
  const label = screen.getByText("Runs");
  expect(label.tagName).toBe("DT");
  expect(label.parentElement?.tagName).toBe("DL");
  expect(screen.getByText("Every hour, on the hour.").tagName).toBe("DD");
});

test("the gutter is fixed and the prose takes the rest, so every sentence starts on one edge", () => {
  render(<Detail label="Takes">Open issues carrying no comment.</Detail>);
  const label = screen.getByText("Takes");
  expect(label.className).toContain("w-hint");
  expect(label.className).toContain("shrink-0");
  const body = screen.getByText("Open issues carrying no comment.");
  expect(body.className).toContain("flex-1");
  expect(body.className).toContain("min-w-0");
});

test("the prose is not truncated, because an aspect the member came to read has to grow", () => {
  render(<Detail label="Writes">One comment per issue.</Detail>);
  const body = screen.getByText("One comment per issue.");
  expect(body.className).not.toContain("truncate");
  expect(body.className).not.toContain("text-ellipsis");
});

test("an aspect holds any content, so a plan can be a list rather than a sentence", () => {
  render(
    <Detail label="Plan">
      <ul>
        <li>Read the issue.</li>
        <li>Name the owner.</li>
      </ul>
    </Detail>,
  );
  expect(screen.getByRole("list").closest("dd")).not.toBeNull();
  expect(screen.getAllByRole("listitem")).toHaveLength(2);
});

test("below the narrow breakpoint the label stands over the prose, so a phone is not a blank column", () => {
  render(<Detail label="Raise">The waitlist is at 100; the draft still says 60.</Detail>);
  const pair = screen.getByText("Raise").parentElement!;
  expect(pair.className).toContain("max-narrow:flex-col");
  expect(pair.className).toContain("max-narrow:gap-2xs");
  // The gutter is a text measure and does not shrink, so on a phone it is wider than the row and
  // squeezes the prose to zero width. Releasing the width is what makes the stack fit.
  expect(screen.getByText("Raise").className).toContain("max-narrow:w-auto");
});
