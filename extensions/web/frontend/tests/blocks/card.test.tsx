import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

import type { CSSProperties } from "react";

import { Card, CardButton, CardContent, CardImage, CardTitle } from "@/blocks/card";
import { CardPage } from "@/blocks/docs/CardPage";
import { CardButtonStates } from "@/blocks/docs/examples/card-button-states";
import { CardStatesInteractive } from "@/blocks/docs/examples/card-states-interactive";

const EXAMPLES = [
  "Default",
  "Variants",
  "With action",
  "Small",
  "Large",
  "Spacing",
  "Image",
  "Edge-to-edge",
  "Item list",
  "Summary with prompts",
  "Mono body",
  "Nested list",
  "Pill",
  "Bubble",
  "Button sizes and states",
  "RTL",
  "States",
  "Every state",
  "Interactive states",
  "API",
];

test("a card is the outline surface at the default inset, so a plain card needs no props", () => {
  const { container } = render(<Card>Body</Card>);
  const card = container.querySelector(".blk-card");
  expect(card?.getAttribute("data-variant")).toBe("outline");
  expect(card?.getAttribute("data-size")).toBe("default");
  expect(card?.getAttribute("data-inline")).toBeNull();
  expect(card?.getAttribute("data-align")).toBeNull();
});

test("variant and size name the surface, so no screen spells the padding or the edge", () => {
  const { container, rerender } = render(
    <Card variant="muted" size="lg">
      Body
    </Card>,
  );
  const card = container.querySelector(".blk-card");
  expect(card?.getAttribute("data-variant")).toBe("muted");
  expect(card?.getAttribute("data-size")).toBe("lg");
  rerender(
    <Card variant="pill" size="sm">
      Body
    </Card>,
  );
  expect(container.querySelector(".blk-card")?.getAttribute("data-variant")).toBe("pill");
  expect(container.querySelector(".blk-card")?.getAttribute("data-size")).toBe("sm");
});

test("the spacing variable reaches the card, so one declaration retunes every inset under it", () => {
  const { container } = render(<Card style={{ "--blk-card-spacing": "20px" } as CSSProperties}>Body</Card>);
  const card = container.querySelector(".blk-card") as HTMLElement;
  expect(card.style.getPropertyValue("--blk-card-spacing")).toBe("20px");
});

test("inline and align state how the card sits in its column, so a bubble needs no wrapper of its own", () => {
  const { container } = render(
    <Card inline align="end">
      Body
    </Card>,
  );
  const card = container.querySelector(".blk-card");
  expect(card?.getAttribute("data-inline")).toBe("true");
  expect(card?.getAttribute("data-align")).toBe("end");
});

test("a title carries its step, so the h1 card and the label card are one component", () => {
  render(
    <>
      <CardTitle>Design sync</CardTitle>
      <CardTitle size="lg">Q2 dividend income</CardTitle>
    </>,
  );
  expect(screen.getByText("Design sync")?.getAttribute("data-size")).toBe("default");
  expect(screen.getByText("Q2 dividend income")?.getAttribute("data-size")).toBe("lg");
});

test("content takes the mono face by attribute, so the Coding lane sets it without a second component", () => {
  render(<CardContent data-font="mono">use client</CardContent>);
  expect(screen.getByText("use client")?.getAttribute("data-font")).toBe("mono");
});

test("bleed is a state of the content, so a table runs to the card edge without a margin at the call site", () => {
  const { container, rerender } = render(<CardContent bleed>Rows</CardContent>);
  expect(container.querySelector(".blk-card-content")?.getAttribute("data-bleed")).toBe("true");
  rerender(<CardContent>Rows</CardContent>);
  expect(container.querySelector(".blk-card-content")?.getAttribute("data-bleed")).toBeNull();
});

test("a card image carries its alt and its aspect, so a picture above the header is one element", () => {
  render(<CardImage src="cover.png" alt="Quarterly revenue cover" ratio={4 / 3} />);
  const picture = screen.getByAltText("Quarterly revenue cover") as HTMLImageElement;
  expect(picture.className).toBe("blk-card-image");
  expect(picture.style.aspectRatio).toBe(String(4 / 3));
});

test("both button variants are one button, and the click reaches the caller", async () => {
  const opened = vi.fn();
  render(
    <>
      <CardButton onClick={opened}>Open notes</CardButton>
      <CardButton variant="secondary">Dismiss</CardButton>
    </>,
  );
  const primary = screen.getByRole("button", { name: "Open notes" });
  expect(primary?.getAttribute("data-variant")).toBe("primary");
  expect(screen.getByRole("button", { name: "Dismiss" })?.getAttribute("data-variant")).toBe("secondary");
  await userEvent.click(primary);
  expect(opened).toHaveBeenCalledOnce();
});

test("a footer pair shares one height, and lg is the step a single closing act takes", () => {
  render(
    <>
      <CardButton>Open notes</CardButton>
      <CardButton variant="secondary">Dismiss</CardButton>
      <CardButton size="lg">View full report</CardButton>
    </>,
  );
  expect(screen.getByRole("button", { name: "Open notes" })?.getAttribute("data-size")).toBe("default");
  expect(screen.getByRole("button", { name: "Dismiss" })?.getAttribute("data-size")).toBe("default");
  expect(screen.getByRole("button", { name: "View full report" })?.getAttribute("data-size")).toBe("lg");
});

test("sm is the step a row of acts takes, and a disabled act refuses the press and says so", async () => {
  const pressed = vi.fn();
  render(
    <>
      <CardButton size="sm" onClick={pressed}>
        Approve
      </CardButton>
      <CardButton size="sm" disabled onClick={pressed}>
        Archive
      </CardButton>
    </>,
  );
  expect(screen.getByRole("button", { name: "Approve" }).getAttribute("data-size")).toBe("sm");
  const shut = screen.getByRole("button", { name: "Archive" });
  expect(shut.getAttribute("aria-disabled")).toBe("true");
  expect((shut as HTMLButtonElement).disabled).toBe(true);
  await userEvent.setup({ pointerEventsCheck: 0 }).click(shut);
  expect(pressed).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Approve" }));
  expect(pressed).toHaveBeenCalledOnce();
});

test("the example refuses the act it has already taken, so approving twice is impossible", async () => {
  const user = userEvent.setup({ pointerEventsCheck: 0 });
  render(<CardButtonStates />);
  await user.click(screen.getByRole("button", { name: "Approve" }));
  const taken = screen.getByRole("button", { name: "Approved" });
  expect((taken as HTMLButtonElement).disabled).toBe(true);
  await user.click(screen.getByRole("button", { name: "Reset" }));
  expect((screen.getByRole("button", { name: "Approve" }) as HTMLButtonElement).disabled).toBe(false);
});

test("the page shows every example, so the reference never names a card it cannot draw", () => {
  render(<CardPage />);
  for (const heading of EXAMPLES) {
    expect(screen.getByRole("heading", { name: heading })).toBeTruthy();
  }
});

test("the card takes the width its container gives it and folds its body from the footer", async () => {
  const user = userEvent.setup();
  const { container } = render(<CardStatesInteractive />);
  const box = container.querySelector(".blk-card")!.parentElement as HTMLElement;
  expect(box.style.maxWidth).toBe("640px");
  expect(container.querySelector(".blk-card")!.getAttribute("style")).toBeNull();

  await user.click(screen.getByRole("button", { name: "320px" }));
  expect((container.querySelector(".blk-card")!.parentElement as HTMLElement).style.maxWidth).toBe("320px");

  expect(container.querySelector(".blk-card-content")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Show the detail" }));
  expect(container.querySelector(".blk-card-content")).not.toBeNull();
  await user.click(screen.getByRole("button", { name: "Hide the detail" }));
  expect(container.querySelector(".blk-card-content")).toBeNull();
});
