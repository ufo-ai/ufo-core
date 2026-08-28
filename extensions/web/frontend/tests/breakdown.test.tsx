import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import {
  Breakdown,
  BreakdownHeader,
  BreakdownLabel,
  BreakdownMark,
  BreakdownName,
  BreakdownRow,
  BreakdownRows,
  BreakdownValue,
} from "@/components/ui/breakdown";

function column() {
  render(
    <Breakdown>
      <BreakdownHeader>
        <BreakdownLabel>Connectors</BreakdownLabel>
        <button type="button">More</button>
      </BreakdownHeader>
      <BreakdownRows>
        <BreakdownRow>
          <BreakdownName>
            <BreakdownMark>
              <svg data-slot="mark" />
            </BreakdownMark>
            GitHub
          </BreakdownName>
          <BreakdownValue>12.3%</BreakdownValue>
        </BreakdownRow>
        <BreakdownRow>
          <BreakdownName>Stripe</BreakdownName>
          <BreakdownValue>4.2%</BreakdownValue>
        </BreakdownRow>
      </BreakdownRows>
    </Breakdown>,
  );
}

const classes = (slot: string) =>
  document.querySelector(`[data-slot="${slot}"]`)!.className.split(" ");

test("a share's figure lands on the column's right edge, so a column of shares is scannable", () => {
  column();
  expect(classes("breakdown-row")).toContain("justify-between");
  expect(classes("breakdown-value")).toContain("shrink-0");
  expect(classes("breakdown-value")).toContain("tabular-nums");
});

test("the name gives way, not the figure the column exists to state", () => {
  column();
  expect(classes("breakdown-name")).toContain("min-w-0");
  expect(classes("breakdown-name")).toContain("truncate");
  expect(classes("breakdown-value")).toContain("whitespace-nowrap");
  expect(classes("breakdown-value")).not.toContain("truncate");
});

test("a row is one glyph deep, so a long name cannot change the column's rhythm", () => {
  column();
  expect(classes("breakdown-row")).toContain("h-(--size-glyph)");
  expect(classes("breakdown-header")).toContain("h-(--size-glyph)");
});

test("the mark takes the glyph square, so no column states that size itself", () => {
  column();
  expect(classes("breakdown-mark")).toContain("size-(--size-glyph)");
  expect(classes("breakdown-mark")).toContain("shrink-0");
});

test("shares sit at a tighter pitch than the column and its heading", () => {
  column();
  expect(classes("breakdown-rows")).toContain("gap-sm");
  expect(classes("breakdown")).toContain("gap-2xl");
});

test("a row needs no mark, so a part with none is still a row", () => {
  column();
  const rows = document.querySelectorAll('[data-slot="breakdown-row"]');
  expect(rows).toHaveLength(2);
  expect(rows[1].querySelector('[data-slot="breakdown-mark"]')).toBeNull();
  expect(screen.getByText("Stripe")).toBeTruthy();
  expect(screen.getByText("4.2%")).toBeTruthy();
});

test("the heading holds an act at its far end without moving the label", () => {
  column();
  expect(classes("breakdown-label")).toContain("flex-1");
  expect(classes("breakdown-label")).toContain("truncate");
  expect(screen.getByRole("button", { name: "More" }).parentElement)
    .toBe(document.querySelector('[data-slot="breakdown-header"]'));
});
