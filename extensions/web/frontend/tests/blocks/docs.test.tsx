import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { Docs, PropsTable } from "@/blocks/docs/Docs";
import { HomePage } from "@/blocks/docs/HomePage";
import { PAGES } from "@/blocks/docs/pages";

const BLOCKS = PAGES.filter((page) => page.slug !== "");

test("the index card for every block counts the examples its page renders", () => {
  const index = render(<HomePage pages={PAGES} />);
  const cards = [...index.container.querySelectorAll(".blk-docs-index-card")];
  expect(cards.map((card) => card.getAttribute("href"))).toEqual(BLOCKS.map((page) => `#/${page.slug}`));

  for (const [position, page] of BLOCKS.entries()) {
    const card = cards[position];
    expect(card.querySelector(".blk-card-title")?.textContent, page.slug).toBe(page.title);
    expect(card.querySelector(".blk-card-description")?.textContent, page.slug).toBeTruthy();
    const stated = Number(card.querySelector(".blk-docs-index-count")?.textContent?.split(" ")[0]);
    const view = render(<>{page.render()}</>);
    expect(view.container.querySelectorAll(".blk-example").length, page.slug).toBe(stated);
    view.unmount();
  }
  index.unmount();
});

test("the shell opens on the index and links every block from the nav", () => {
  location.hash = "";
  const { container } = render(<Docs />);
  const nav = container.querySelector(".blk-docs-nav")!;
  expect([...nav.querySelectorAll("a")].map((link) => link.getAttribute("href"))).toEqual([
    "#/",
    ...BLOCKS.map((page) => `#/${page.slug}`),
  ]);
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Blocks");
});

test("a props table scrolls inside its own container rather than widening the page", () => {
  const { container } = render(<PropsTable rows={[{ name: "size", type: "number", description: "The icon size." }]} />);
  expect(container.querySelector(".blk-props-scroll > table.blk-props")).not.toBeNull();
});
