import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { Meter } from "@/components/ui/meter";
import type { MeterPart } from "@/components/ui/meter";

const PARTS: MeterPart[] = [
  { label: "Committed", value: 40, tone: "primary" },
  { label: "Forecast", value: 35, tone: "secondary" },
];

const width = (el: Element) => (el as HTMLElement).style.width;
const parts = () => Array.from(document.querySelectorAll('[data-slot="meter-part"]'));

test("each share takes the width it is worth, and the rest stays as the track", () => {
  render(<Meter label="Revenue against plan" parts={PARTS} of={100} />);
  expect(parts().map(width)).toEqual(["40%", "35%"]);
  expect(screen.getByRole("meter").className).toContain("bg-fill");
});

test("the two tones are the palette's accents, so no page spells either colour", () => {
  render(<Meter label="Revenue against plan" parts={PARTS} of={100} />);
  expect(parts()[0].className).toContain("bg-live");
  expect(parts()[1].className).toContain("bg-blocked");
});

test("the bar answers once, as its shares out of its whole", () => {
  render(<Meter label="Revenue against plan" parts={PARTS} of={100} />);
  const bar = screen.getByRole("meter");
  expect(bar.getAttribute("aria-valuemax")).toBe("100");
  expect(bar.getAttribute("aria-valuenow")).toBe("75");
  expect(bar.getAttribute("aria-valuetext")).toBe("Committed 40%, Forecast 35%");
  // The shares themselves say nothing, so a reader is not told the same division twice.
  expect(parts().every((part) => part.getAttribute("aria-hidden") === "true")).toBe(true);
  expect(bar.textContent).toBe("");
});

/** A caller counts in whatever it counts in — this one in the cents behind "$31.40 of 10000" — so
 *  reading its values out states a figure that is on no screen. The share is what the bar draws and
 *  what holds in any unit. */
test("a share is announced as its share, not as the units the caller counts in", () => {
  render(
    <Meter
      label="Spend against the cap"
      parts={[
        { label: "Models", value: 2680, tone: "primary" },
        { label: "Sandboxes", value: 460, tone: "secondary" },
      ]}
      of={10000}
    />,
  );
  expect(screen.getByRole("meter").getAttribute("aria-valuetext")).toBe(
    "Models 27%, Sandboxes 5%",
  );
});

test("a share of nothing draws nothing, so a part that has not started is not a sliver", () => {
  render(
    <Meter
      label="Revenue against plan"
      parts={[...PARTS, { label: "Renewal", value: 0, tone: "primary" }]}
      of={100}
    />,
  );
  expect(parts()).toHaveLength(2);
});

test("shares past the whole are held at it, so the bar cannot state an impossible proportion", () => {
  render(
    <Meter
      label="Spend against cap"
      parts={[
        { label: "Models", value: 90, tone: "primary" },
        { label: "Sandboxes", value: 60, tone: "secondary" },
      ]}
      of={100}
    />,
  );
  const drawn = parts().map((part) => Number.parseFloat(width(part)));
  expect(drawn.reduce((sum, each) => sum + each, 0)).toBeCloseTo(100, 5);
  expect(screen.getByRole("meter").getAttribute("aria-valuenow")).toBe("100");
});

test("the bar shrinks for whatever stands at the end of its row", () => {
  render(
    <div>
      <Meter label="Revenue against plan" parts={PARTS} of={100} />
      <span>faces</span>
    </div>,
  );
  const classes = screen.getByRole("meter").className;
  expect(classes).toContain("w-full");
  expect(classes).toContain("min-w-0");
});

test("a whole of nothing does not divide by zero, and never states more than all of it", () => {
  render(<Meter label="Nothing counted yet" parts={PARTS} of={0} />);
  expect(parts().map(width).every((each) => each !== "NaN%")).toBe(true);
  // A bar cannot be further through a whole than the whole: read aloud, `now` over `max` is the
  // answer, and 1 of 0 is not one a member can take anything from.
  const bar = screen.getByRole("meter");
  const now = Number(bar.getAttribute("aria-valuenow"));
  const most = Number(bar.getAttribute("aria-valuemax"));
  expect(now).toBeLessThanOrEqual(most);
});

test("two parts may carry one label, and the bar still draws both", () => {
  render(
    <Meter
      label="Spend against cap"
      parts={[
        { label: "Models", value: 30, tone: "primary" },
        { label: "Models", value: 20, tone: "secondary" },
      ]}
      of={100}
    />,
  );
  expect(parts().map(width)).toEqual(["30%", "20%"]);
});
