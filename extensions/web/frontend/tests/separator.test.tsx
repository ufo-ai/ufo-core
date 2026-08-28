import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { Separator } from "@/components/ui/separator";

test("a horizontal rule states the break it draws, so it is not a line only the eye is given", () => {
  render(<Separator />);
  const rule = screen.getByRole("separator");
  expect(rule.getAttribute("aria-orientation")).toBe("horizontal");
  expect(rule.className).toContain("border-t");
  expect(rule.className).toContain("border-edge");
});

test("a vertical rule turns its border with it, so the orientation is one answer", () => {
  render(<Separator orientation="vertical" />);
  const rule = screen.getByRole("separator");
  expect(rule.getAttribute("aria-orientation")).toBe("vertical");
  expect(rule.className).toContain("border-l");
  expect(rule.className).not.toContain("border-t");
});

test("the rule is its own empty element, so neither side of it carries a border", () => {
  render(
    <div>
      <p>above</p>
      <Separator />
      <p>below</p>
    </div>,
  );
  const rule = screen.getByRole("separator");
  expect(rule.previousElementSibling?.textContent).toBe("above");
  expect(rule.nextElementSibling?.textContent).toBe("below");
  expect(rule.textContent).toBe("");
});

test("a rule never shrinks, so a crowded row cannot squeeze it away", () => {
  render(<Separator orientation="vertical" />);
  expect(screen.getByRole("separator").className).toContain("shrink-0");
});
