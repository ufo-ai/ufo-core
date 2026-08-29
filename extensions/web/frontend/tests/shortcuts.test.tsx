import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";

import { Shortcuts } from "@/views/Shortcuts";

const TITLE = "Keyboard shortcuts";

function keysFor(act: string): string[] {
  const row = within(screen.getByRole("dialog", { name: TITLE }))
    .getAllByText(act)[0]
    .closest("[data-slot=shortcut]");
  return [...(row?.querySelectorAll("kbd") ?? [])].map((key) => key.textContent ?? "");
}

test("the chord lists every chord the portal answers", async () => {
  render(<Shortcuts />);

  expect(screen.queryByRole("dialog")).toBeNull();
  await userEvent.keyboard("?");

  expect(await screen.findByRole("dialog", { name: TITLE })).toBeTruthy();
  expect(keysFor("Next lane")).toEqual(["]"]);
  expect(keysFor("Previous lane")).toEqual(["["]);
  expect(keysFor("Search")).toEqual(["⌘", "K"]);
  expect(keysFor("Leave the message box")).toEqual(["Esc"]);
});

/** A member typing `?` into the composer is typing a character, not pressing a chord. */
test("the chord typed into a field stays a character", async () => {
  render(
    <>
      <textarea aria-label="Message" />
      <Shortcuts />
    </>,
  );

  const composer = screen.getByLabelText<HTMLTextAreaElement>("Message");
  await userEvent.type(composer, "?");

  expect(screen.queryByRole("dialog")).toBeNull();
  expect(composer.value).toBe("?");
});

test("Escape shuts the list", async () => {
  render(<Shortcuts />);
  await userEvent.keyboard("?");
  expect(await screen.findByRole("dialog", { name: TITLE })).toBeTruthy();

  await userEvent.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

test("the chord shuts the list it opened", async () => {
  render(<Shortcuts />);
  await userEvent.keyboard("?");
  expect(await screen.findByRole("dialog", { name: TITLE })).toBeTruthy();

  await userEvent.keyboard("?");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

/** The chord is bare: a command held over it is a keyboard shortcut of the browser's, not ours. */
test("the command chord leaves the list shut", async () => {
  render(<Shortcuts />);

  await userEvent.keyboard("{Meta>}?{/Meta}");
  expect(screen.queryByRole("dialog")).toBeNull();

  await userEvent.keyboard("?");
  expect(await screen.findByRole("dialog", { name: TITLE })).toBeTruthy();
});

test("the chord pressed inside an open list is left to the list", async () => {
  render(<Shortcuts />);
  const list = document.createElement("div");
  list.setAttribute("role", "listbox");
  const option = document.createElement("button");
  list.appendChild(option);
  document.body.appendChild(list);
  option.focus();

  await userEvent.keyboard("?");

  expect(screen.queryByRole("dialog", { name: "Keyboard shortcuts" })).toBeNull();
  list.remove();
});

