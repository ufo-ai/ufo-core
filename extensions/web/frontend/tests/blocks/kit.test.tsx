import { readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { BlockRoot, Item, ItemContent, ItemGroup, ItemTitle, StatGrid, StatTile } from "@/blocks/kit";

const TOKENS = join(process.cwd(), "src", "blocks", "tokens.css");
const DOCS = join(process.cwd(), "src", "blocks", "docs", "docs.css");

test("the published entry composes a page body the way an app page reaches it", () => {
  const { container } = render(
    <BlockRoot>
      <StatGrid columns={2}>
        <StatTile label="Waiting" value="3" />
      </StatGrid>
      <ItemGroup>
        <Item>
          <ItemContent>
            <ItemTitle>Rotate the signing key</ItemTitle>
          </ItemContent>
        </Item>
      </ItemGroup>
    </BlockRoot>,
  );
  expect(container.firstElementChild?.className).toBe("blk-root");
  expect(screen.getByText("Rotate the signing key").className).toContain("blk-item-title");
  expect(screen.getByText("Waiting")).toBeTruthy();
});

/** Measured: with the scheme declared here, a host that forces dark by class kept the blocks light —
 *  an unlayered :root rule outranks the theme's @layer base, whichever file loads first. */
test("the shared tokens leave the colour scheme to whoever owns the root", () => {
  expect(readFileSync(TOKENS, "utf8")).not.toContain("color-scheme");
  expect(readFileSync(DOCS, "utf8")).toContain(":root { color-scheme: light dark; }");
});

/** Measured: at #919090 all nine recipe-built pages reported 3.03:1 and 2.87:1 on un-slotted text
 *  against the audit's 4.5 floor, and app.tsx may carry no raw colour, style tag or data-slot. */
test("block secondary text clears the audit's body floor on the page and on a card", () => {
  const tokens = readFileSync(TOKENS, "utf8");
  const light = /--blk-text-2:\s*light-dark\(\s*(#[0-9a-fA-F]{6})/.exec(tokens);
  expect(light).not.toBeNull();

  const luminance = (hex: string): number => {
    const channel = (pair: string): number => {
      const value = parseInt(pair, 16) / 255;
      return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
    };
    const [r, g, b] = [hex.slice(1, 3), hex.slice(3, 5), hex.slice(5, 7)].map(channel);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const ratio = (a: string, b: string): number => {
    const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (high + 0.05) / (low + 0.05);
  };

  const ink = light![1];
  for (const background of ["#faf9f7", "#f4f3f2"]) {
    expect(ratio(ink, background)).toBeGreaterThanOrEqual(4.5);
  }
});
