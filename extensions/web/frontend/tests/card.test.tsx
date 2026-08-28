import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Chart } from "@/components/ui/chart";

const slots = () =>
  Array.from(document.querySelectorAll("[data-slot^=card]")).map((part) =>
    part.getAttribute("data-slot"),
  );

const slot = (name: string) => document.querySelector(`[data-slot=${name}]`)!;

const classes = (name: string) => slot(name).className.split(" ");

test("a card stacks its parts, each drawn as its own slot", () => {
  render(
    <Card>
      <CardHeader>
        <CardTitle>Weekly update</CardTitle>
        <CardAction>2 open</CardAction>
      </CardHeader>
      <CardDescription>Three reviews closed since Monday.</CardDescription>
      <CardContent>Rows</CardContent>
      <CardFooter>Read</CardFooter>
    </Card>,
  );

  expect(slots()).toEqual([
    "card",
    "card-header",
    "card-title",
    "card-action",
    "card-description",
    "card-content",
    "card-footer",
  ]);
  expect(Array.from(slot("card").children).map((part) => part.getAttribute("data-slot"))).toEqual([
    "card-header",
    "card-description",
    "card-content",
    "card-footer",
  ]);
});

/** jsdom lays nothing out, so the class is the contract: a head without a fixed height is a band
 *  its title's line box opens, and a grid of cards then aligns title to title only while every
 *  title runs to the same length. */
test("the head is one glyph-high line, with the act at its trailing end", () => {
  render(
    <Card>
      <CardHeader>
        <CardTitle>Weekly update</CardTitle>
        <CardAction>2 open</CardAction>
      </CardHeader>
    </Card>,
  );

  const header = slot("card-header");
  expect(header.className).toContain("h-(--size-glyph)");
  expect(header.className).toContain("items-center");
  expect(header.className).toContain("justify-between");
  expect(header.lastElementChild).toBe(slot("card-action"));
  expect(slot("card-action").className).toContain("ml-auto");
  expect(slot("card-action").className).toContain("shrink-0");
});

test("the title is cut at the head's width and the prose beneath it wraps", () => {
  render(
    <Card>
      <CardHeader>
        <CardTitle>A title longer than the card it heads</CardTitle>
      </CardHeader>
      <CardDescription>A sentence that runs past the width of the card.</CardDescription>
    </Card>,
  );

  expect(classes("card-title")).toContain("truncate");
  expect(classes("card-title")).toContain("min-w-0");
  expect(classes("card-description")).not.toContain("truncate");
});

test("the card carries its own fill, so it reads over a filled band", () => {
  render(<Card>Update</Card>);

  expect(slot("card").className).toContain("bg-card");
  expect(slot("card").className).toContain("text-card-foreground");
});

test("the ground is the tone's, and only the default tone draws a hairline", () => {
  render(
    <>
      <Card>Update</Card>
      <Card tone="attention">Overdue</Card>
    </>,
  );

  const [plain, tinted] = Array.from(document.querySelectorAll("[data-slot=card]")).map((card) =>
    card.className.split(" "),
  );
  expect(plain).toContain("border-edge");
  expect(plain).toContain("bg-card");
  expect(tinted).toContain("bg-attention");
  expect(tinted).toContain("border-transparent");
  expect(tinted).not.toContain("border-edge");
  expect(tinted).not.toContain("bg-card");
  // The tint is where the card ends, so the hairline goes and the box it measured stays: dropping
  // the border outright would set a tinted card two pixels narrower than the plain one beside it.
  expect(tinted).toContain("border");
});

test("a card holding a plot draws one ground: the card's", () => {
  render(
    <Card>
      <CardHeader>
        <CardTitle>Open pull requests</CardTitle>
      </CardHeader>
      <Chart label="Open pull requests each day" points={[1, 4, 2, 6]} />
    </Card>,
  );

  expect(classes("card")).toContain("bg-card");
  expect(classes("card")).toContain("rounded-card");
  const plot = document.querySelector("[data-slot=chart]")!.className.split(" ");
  expect(plot.filter((name) => name.startsWith("bg-") || name.startsWith("rounded-"))).toEqual([]);
});

test("a card inside a card's content is one box inside one box", () => {
  render(
    <Card>
      <CardHeader>
        <CardTitle>Weekly update</CardTitle>
      </CardHeader>
      <CardContent>
        <Card>
          <CardHeader>
            <CardTitle>Reviews</CardTitle>
          </CardHeader>
        </Card>
      </CardContent>
    </Card>,
  );

  const cards = document.querySelectorAll("[data-slot=card]");
  expect(cards).toHaveLength(2);
  const content = slot("card-content");
  expect(content.className).not.toMatch(/(?:^|\s)-?p[xytrbl]?-/);
  expect(content.firstElementChild).toBe(cards[1]);
  expect(cards[1].className).toContain("p-2xl");
  expect(cards[1].className).toContain("border-edge");
  expect(screen.getByText("Reviews")).toBeTruthy();
});

test("a part keeps its own shape while the caller's class replaces what it names", () => {
  render(
    <Card className="p-0">
      <CardHeader className="h-(--size-row)">
        <CardTitle className="text-body">Weekly update</CardTitle>
      </CardHeader>
    </Card>,
  );

  expect(classes("card")).toContain("p-0");
  expect(classes("card")).not.toContain("p-2xl");
  expect(classes("card")).toContain("border-edge");
  expect(classes("card-header")).toContain("h-(--size-row)");
  expect(classes("card-header")).not.toContain("h-(--size-glyph)");
  expect(classes("card-title")).toContain("text-body");
  expect(classes("card-title")).not.toContain("text-label");
  expect(classes("card-title")).toContain("truncate");
});

test("a card takes the attributes that name it, so it can stand as a labelled region", () => {
  render(
    <Card role="group" aria-label="Weekly update" id="update">
      <CardContent>Rows</CardContent>
    </Card>,
  );

  const card = screen.getByRole("group", { name: "Weekly update" });
  expect(card.id).toBe("update");
  expect(card.getAttribute("data-slot")).toBe("card");
});
