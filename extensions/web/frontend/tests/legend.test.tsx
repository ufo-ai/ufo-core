import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { Legend, LegendItem } from "@/components/ui/legend";
import type { LegendTone } from "@/components/ui/legend";

/** The tone and the class it paints, as a record keyed by the union: a name the component drops and
 *  a name it grows are both a typecheck failure here, so "exactly these three" is `tsc`'s and not a
 *  count a reader has to keep. */
const TONES: Record<LegendTone, string> = {
  primary: "bg-live",
  secondary: "bg-blocked",
  muted: "bg-ink-faint",
};

const BANDS: readonly [LegendTone, string][] = [
  ["muted", "Earlier weeks"],
  ["secondary", "Last week"],
  ["primary", "This week"],
];

const drawn = () => (
  <Legend>
    {BANDS.map(([tone, name]) => (
      <LegendItem key={name} tone={tone}>
        {name}
      </LegendItem>
    ))}
  </Legend>
);

const swatches = () => Array.from(document.querySelectorAll('[data-slot="legend-swatch"]'));

test("the names come in the order the graphic bands them", () => {
  render(drawn());
  expect(screen.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
    "Earlier weeks",
    "Last week",
    "This week",
  ]);
});

test("every tone the graphic can draw has a swatch class, and no page spells a colour", () => {
  for (const [tone, paint] of Object.entries(TONES)) {
    const { unmount } = render(
      <Legend>
        <LegendItem tone={tone as LegendTone}>Named</LegendItem>
      </Legend>,
    );
    expect(swatches()[0].className).toContain(paint);
    expect(swatches()[0].getAttribute("data-tone")).toBe(tone);
    unmount();
  }
});

test("each entry paints the tone it was given, in the order it was given", () => {
  render(drawn());
  expect(swatches().map((swatch) => swatch.getAttribute("data-tone"))).toEqual([
    "muted",
    "secondary",
    "primary",
  ]);
  expect(swatches().map((swatch) => swatch.className.includes(TONES.muted))).toEqual([
    true,
    false,
    false,
  ]);
});

test("the list is the legend, and the swatch says nothing a second time", () => {
  render(drawn());
  expect(screen.getByRole("list").getAttribute("data-slot")).toBe("legend");
  expect(swatches()).toHaveLength(3);
  expect(swatches().every((swatch) => swatch.getAttribute("aria-hidden") === "true")).toBe(true);
  expect(swatches().every((swatch) => swatch.textContent === "")).toBe(true);
  expect(screen.getByText("This week")).toBeDefined();
});

test("a name gives way after the swatch, so the word that carries the point is not the one cut", () => {
  render(drawn());
  expect(screen.getByRole("list").className).toContain("flex-wrap");
  expect(swatches().every((swatch) => swatch.className.includes("shrink-0"))).toBe(true);
});
