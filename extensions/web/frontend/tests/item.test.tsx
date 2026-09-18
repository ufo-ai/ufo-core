import { readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Fragment } from "react";
import { expect, test } from "vitest";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemLink,
  ItemMedia,
  ItemPress,
  ItemSeparator,
  ItemTitle,
  MarkTile,
  itemVariants,
} from "@/components/ui/item";
import { cn } from "@/lib/cn";
import { rowControl } from "@/kernel/row";

const GAP = /(?<![\w-])gap-/;
const NO_INSET = /(?<![\w-])p-0(?![\w-])/;
const NO_GAP = /(?<![\w-])gap-0(?![\w-])/;
const OVERLAP = "-ml-2xs";

const THEME = readFileSync(join(import.meta.dirname, "..", "src", "theme.css"), "utf8");

function size(token: string): number {
  const declared = new RegExp("--size-" + token + ":\\s*(\\d+)px").exec(THEME);
  if (!declared) throw new Error("the theme declares no --size-" + token);
  return Number(declared[1]);
}

const MEETINGS = [
  {
    at: "09:30",
    title: "Weekly review",
    note: "Last week's actions, and what moved since.",
    attendees: ["Ada", "Bo", "Cyd"],
  },
  {
    at: "14:00",
    title: "Pricing",
    note: "The two plans, and which accounts sit between them.",
    attendees: ["Ada", "Dov"],
  },
];

function Faces({ people }: { people: string[] }) {
  return (
    <span className="flex items-center">
      {people.map((person, index) => (
        <Avatar key={person} title={person} className={cn("border border-card", index && OVERLAP)}>
          <AvatarFallback>{person.slice(0, 1)}</AvatarFallback>
        </Avatar>
      ))}
    </span>
  );
}

function Meetings() {
  return (
    <Card rows>
      <ItemGroup aria-label="Next meetings">
      {MEETINGS.map((meeting, index) => (
        <Fragment key={meeting.title}>
          {index ? <ItemSeparator /> : null}
          <Item>
            <ItemMedia>
              <Badge>{meeting.at}</Badge>
            </ItemMedia>
            <ItemContent>
              <ItemTitle>{meeting.title}</ItemTitle>
              <ItemDescription>{meeting.note}</ItemDescription>
            </ItemContent>
            <ItemActions>
              <Faces people={meeting.attendees} />
              <Button variant="row">Brief</Button>
            </ItemActions>
          </Item>
        </Fragment>
        ))}
      </ItemGroup>
    </Card>
  );
}

test("the meetings card is one bordered card of ruled rows, each led by its moment", () => {
  render(<Meetings />);
  const group = screen.getByRole("list", { name: "Next meetings" });
  const card = group.closest("[data-slot=card]")!;
  expect(card.className).toContain("border-edge");
  expect(card.className).toContain("rounded-card");
  expect(group.className).not.toContain("border");

  const rows = group.querySelectorAll("[data-slot=item]");
  expect(rows.length).toBe(MEETINGS.length);
  const rules = group.querySelectorAll("[aria-hidden]");
  expect(rules.length).toBe(MEETINGS.length - 1);
  for (const rule of rules) expect(rule.className).toContain("border-t");

  const [first] = rows;
  const parts = [...first.children].map((part) => part.getAttribute("data-slot"));
  expect(parts).toEqual(["item-media", null, "item-actions"]);
  expect(within(first as HTMLElement).getByText("09:30").getAttribute("data-slot")).toBe("badge");
  expect(within(first as HTMLElement).getByText("Weekly review").getAttribute("data-part")).toBe(
    "primary",
  );
  expect(within(first as HTMLElement).getByText(MEETINGS[0].note)).toBeTruthy();
});

test("a row ends with its faces and its act, and the actions' gap does not undo the overlap", () => {
  render(<Meetings />);
  const rows = screen.getByRole("list", { name: "Next meetings" }).querySelectorAll(
    "[data-slot=item]",
  );
  const acts = [...rows[0].children].at(-1) as HTMLElement;
  expect(acts.className).toContain("gap-sm");

  const [stack, act] = [...acts.children];
  expect(act.textContent).toBe("Brief");
  const faces = [...stack.children];
  expect(faces.length).toBe(MEETINGS[0].attendees.length);
  expect(faces[0].className).not.toContain(OVERLAP);
  for (const face of faces.slice(1)) expect(face.className).toContain(OVERLAP);
  expect(GAP.test(stack.className)).toBe(false);
});

test("the leading slot holds a moment, a member, or a stack of them", () => {
  render(
    <ItemGroup>
      <Item>
        <ItemMedia>
          <Badge>Tue</Badge>
        </ItemMedia>
        <ItemContent>
          <ItemTitle>Standup</ItemTitle>
        </ItemContent>
      </Item>
      <Item>
        <ItemMedia>
          <Avatar title="Ada">
            <AvatarFallback>A</AvatarFallback>
          </Avatar>
        </ItemMedia>
        <ItemContent>
          <ItemTitle>Ada</ItemTitle>
        </ItemContent>
      </Item>
      <Item>
        <ItemMedia>
          <Faces people={["Ada", "Bo"]} />
        </ItemMedia>
        <ItemContent>
          <ItemTitle>Pricing</ItemTitle>
        </ItemContent>
      </Item>
    </ItemGroup>,
  );
  const slots = document.querySelectorAll("[data-slot=item-media]");
  expect(slots.length).toBe(3);
  expect(within(slots[0] as HTMLElement).getByText("Tue").getAttribute("data-slot")).toBe("badge");
  expect(slots[1].querySelector("[data-slot=avatar]")).toBeTruthy();
  expect(slots[2].querySelectorAll("[data-slot=avatar]").length).toBe(2);
});

test("the leading slot sets no gap, so a stack overlaps by the measure it spells", () => {
  render(
    <ItemGroup>
      <Item>
        <ItemMedia>
          <Avatar title="Ada">
            <AvatarFallback>A</AvatarFallback>
          </Avatar>
          <Avatar title="Bo" className={OVERLAP}>
            <AvatarFallback>B</AvatarFallback>
          </Avatar>
        </ItemMedia>
        <ItemContent>
          <ItemTitle>Pricing</ItemTitle>
        </ItemContent>
      </Item>
    </ItemGroup>,
  );
  const slot = document.querySelector("[data-slot=item-media]") as HTMLElement;
  expect(GAP.test(slot.className)).toBe(false);
  expect(slot.className).toContain("shrink-0");
  const faces = [...slot.children];
  expect(faces[1].className).toContain(OVERLAP);
});

test("a pressed row keeps its name, because only a row that wraps widens its actions", () => {
  render(
    <ItemGroup>
      <Item size="flush">
        <ItemPress onPress={() => {}}>
          <ItemContent>
            <ItemTitle>pricing-review.pdf</ItemTitle>
          </ItemContent>
          <ItemActions>
            <span>›</span>
          </ItemActions>
        </ItemPress>
      </Item>
    </ItemGroup>,
  );
  const actions = document.querySelector("[data-slot=item-actions]")!;
  expect(actions.className).not.toContain("w-full");
  expect(actions.closest("[data-slot=item]")!.className).toContain(
    "@max-md:*:data-[slot=item-actions]:w-full",
  );
  expect(actions.parentElement!.getAttribute("data-slot")).toBeNull();
});

test("a dense row and the act that fills a flush one carry one inset", () => {
  render(
    <ItemGroup>
      <Item size="row">
        <ItemContent>
          <ItemTitle>Read</ItemTitle>
        </ItemContent>
      </Item>
      <Item size="flush">
        <ItemPress onPress={() => {}}>
          <ItemContent>
            <ItemTitle>Pressed</ItemTitle>
          </ItemContent>
        </ItemPress>
      </Item>
    </ItemGroup>,
  );
  const inset = (element: Element) =>
    [...element.classList].filter((name) => /^(gap|p|px|py)-/.test(name)).sort();

  const read = screen.getByText("Read").closest("[data-slot=item]")!;
  expect(inset(read)).toEqual(["gap-sm", "px-2xl", "py-sm"]);
  expect(inset(screen.getByRole("button"))).toEqual(inset(read));
});

test("a row draws no edge, so the card's border is the group's and the rules the separator's", () => {
  const classes = itemVariants({ variant: "default", size: "default" });
  expect(classes).not.toContain("border");
  expect(classes).not.toContain("bg-");
  expect(classes).toContain("gap-2xl");
  expect(classes).toContain("px-2xl");
  expect(classes).toContain("py-2xl");
});

test("a held row is marked by ground on the fill step, never by an edge inside the card's", () => {
  render(
    <ItemGroup>
      <Item variant="muted" aria-current>
        <ItemContent>
          <ItemTitle>GitHub</ItemTitle>
        </ItemContent>
      </Item>
    </ItemGroup>,
  );
  const row = document.querySelector("[data-slot=item]") as HTMLElement;
  expect(row.className).toContain("bg-fill");
  expect(row.className).not.toContain("border");
  expect(row.className).toContain("px-2xl");
});

test("a flush row gives its inset to the child that carries the press", () => {
  const classes = itemVariants({ size: "flush" });
  expect(classes).toMatch(NO_INSET);
  expect(classes).toMatch(NO_GAP);
  expect(classes).not.toContain("px-2xl");
  expect(classes).not.toContain("py-2xl");
});

test("a caller's own inset still beats the row's, so a row already flushed stays flush", () => {
  render(
    <ItemGroup>
      <Item className="p-0">
        <ItemContent>
          <ItemTitle>Weekly digest</ItemTitle>
        </ItemContent>
      </Item>
    </ItemGroup>,
  );
  const row = document.querySelector("[data-slot=item]") as HTMLElement;
  expect(row.className).toMatch(NO_INSET);
  expect(row.className).not.toContain("px-2xl");
  expect(row.className).not.toContain("py-2xl");
});

test("a row hands the list item every attribute the press needs", () => {
  render(
    <ItemGroup>
      <Item role="button" tabIndex={0} aria-label="Open GitHub" className="hover:bg-fill">
        <ItemContent>
          <ItemTitle>GitHub</ItemTitle>
        </ItemContent>
      </Item>
    </ItemGroup>,
  );
  const row = screen.getByRole("button", { name: "Open GitHub" });
  expect(row.tagName).toBe("LI");
  expect(row.getAttribute("tabindex")).toBe("0");
  expect(row.className).toContain("hover:bg-fill");
  expect(row.className).toContain("items-center");
});

test("a compact mark tile is the control's square, and a full one the touch target's", () => {
  expect(size("control")).toBeLessThan(size("touch"));
  render(
    <ItemGroup>
      <Item size="row">
        <ItemMedia>
          <MarkTile compact>
            <span>c</span>
          </MarkTile>
        </ItemMedia>
        <ItemContent>
          <ItemTitle>notes.md</ItemTitle>
        </ItemContent>
      </Item>
      <Item>
        <ItemMedia>
          <MarkTile>
            <span>f</span>
          </MarkTile>
        </ItemMedia>
        <ItemContent>
          <ItemTitle>GitHub</ItemTitle>
        </ItemContent>
      </Item>
    </ItemGroup>,
  );
  const [compact, full] = [...document.querySelectorAll("[data-slot=mark]")];
  expect(compact.className).toContain("size-(--size-control)");
  expect(compact.className).not.toContain("size-(--size-touch)");
  expect(full.className).toContain("size-(--size-touch)");
});

test("a row's act fills it, so the words and the mark after them open the one record", async () => {
  const pressed: string[] = [];
  render(
    <ItemGroup>
      <Item size="flush">
        <ItemLink href="#/a/weekly-digest">
          <ItemContent>
            <ItemTitle>Weekly digest</ItemTitle>
          </ItemContent>
          <ItemActions>
            <span>link mark</span>
          </ItemActions>
        </ItemLink>
      </Item>
      <Item size="flush">
        <ItemPress onPress={() => pressed.push("report.csv")}>
          <ItemContent>
            <ItemTitle>report.csv</ItemTitle>
          </ItemContent>
          <ItemActions>
            <span>press mark</span>
          </ItemActions>
        </ItemPress>
      </Item>
    </ItemGroup>,
  );

  await userEvent.click(screen.getByText("Weekly digest"));
  expect(location.hash).toBe("#/a/weekly-digest");
  location.hash = "";

  await userEvent.click(screen.getByText("link mark"));
  expect(location.hash).toBe("#/a/weekly-digest");

  await userEvent.click(screen.getByText("report.csv"));
  await userEvent.click(screen.getByText("press mark"));
  expect(pressed).toEqual(["report.csv", "report.csv"]);
});

test("a row's own act keeps its press, so the row behind it does not open too", async () => {
  const opened: string[] = [];
  const connected: string[] = [];
  render(
    <ItemGroup>
      <Item {...rowControl(() => opened.push("GitHub"))}>
        <ItemContent>
          <ItemTitle>GitHub</ItemTitle>
        </ItemContent>
        <ItemActions>
          <Button variant="row" onClick={() => connected.push("GitHub")}>
            Connect
          </Button>
        </ItemActions>
      </Item>
    </ItemGroup>,
  );

  await userEvent.click(screen.getByText("GitHub"));
  expect(opened).toEqual(["GitHub"]);

  await userEvent.click(screen.getByRole("button", { name: "Connect" }));
  expect(connected).toEqual(["GitHub"]);
  expect(opened).toEqual(["GitHub"]);
});
