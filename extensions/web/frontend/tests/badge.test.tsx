import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { Badge, badgeVariants } from "@/components/ui/badge";

const INK = /(?<![\w-])text-ink(?![\w-])/;

test("a badge is a pill on the fill step, so a state reads as a chip and not as an act", () => {
  render(<Badge>Draft</Badge>);
  const classes = screen.getByText("Draft").className;
  expect(classes).toContain("bg-fill");
  expect(classes).toContain("rounded-row");
  expect(classes).toContain("text-small");
  expect(classes).toContain("text-ink-quiet");
  expect(classes).not.toContain("border");
});

test("the attention tone names the second accent, so no screen spells the colour itself", () => {
  const classes = badgeVariants({ tone: "attention" });
  expect(classes).toContain("bg-attention");
  expect(classes).toMatch(INK);
  expect(classes).not.toContain("bg-fill");
  expect(classes).not.toContain("text-ink-soft");
});

test("both tones are the same pill, so a row changing state does not change shape", () => {
  const shape = ["inline-flex", "items-center", "justify-center", "h-4xl", "rounded-row", "px-sm", "text-small"];
  for (const tone of ["default", "attention"] as const) {
    const classes = badgeVariants({ tone });
    for (const utility of shape) expect(classes).toContain(utility);
  }
});

test("a badge keeps its width, so the label beside it is what a squeezed cell cuts", () => {
  render(<Badge tone="attention">Late</Badge>);
  const classes = screen.getByText("Late").className;
  expect(classes).toContain("shrink-0");
  expect(classes).toContain("whitespace-nowrap");
});
