import { render } from "@testing-library/react";
import { expect, test } from "vitest";

import { OrbitMark } from "@/kernel/messages";

test("the orbit mark is three decorative lights, findable by slot", () => {
  const { container } = render(<OrbitMark className="mr-xs" />);

  const mark = container.querySelector("[data-slot=orbit]")!;
  expect(mark.getAttribute("aria-hidden")).toBe("true");
  expect(mark.className).toContain("shrink-0");
  expect(mark.className).toContain("mr-xs");
  expect(mark.querySelectorAll(":scope > span")).toHaveLength(3);
  expect(mark.textContent).toBe("");
});
