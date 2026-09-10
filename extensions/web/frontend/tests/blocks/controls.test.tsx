import { readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

import { IconDots } from "@tabler/icons-react";

import { Avatar, AvatarStack } from "@/blocks/avatar";
import { Checkbox } from "@/blocks/checkbox";
import { Menu, MenuButton, MenuContent, MenuItem, MenuTrigger } from "@/blocks/menu";
import { ControlsPage } from "@/blocks/docs/ControlsPage";
import { ControlsMenuFocus } from "@/blocks/docs/examples/controls-menu-focus";
import { ControlsStatesInteractive } from "@/blocks/docs/examples/controls-states-interactive";

const CHECKBOX_CSS = readFileSync(join(import.meta.dirname, "..", "..", "src", "blocks", "checkbox.css"), "utf8");

const SNIPPET = "Blocked on the credential audit";

const EXAMPLES = [
  "Avatar sizes",
  "Avatar stack",
  "Dropdown menu",
  "Menu button",
  "Menu keeping focus",
  "Checkbox states",
  "States",
  "Every state",
  "Interactive states",
  "API",
];

test("an avatar names who it stands for and states its step, so a face is read and sized by the stylesheet", () => {
  render(<Avatar alt="Ana Ruiz" fallback="AR" size={24} />);
  const face = screen.getByRole("img", { name: "Ana Ruiz" });
  expect(face.getAttribute("data-size")).toBe("24");
  expect(face.textContent).toBe("AR");
});

test("a stack draws every face it is given, so the overlap is the stylesheet's and the count is the caller's", () => {
  const { container } = render(
    <AvatarStack>
      <Avatar alt="Ana Ruiz" fallback="AR" />
      <Avatar alt="Sam Patel" fallback="SP" />
      <Avatar alt="Priya Nair" fallback="PN" />
    </AvatarStack>,
  );
  expect(container.querySelectorAll(".blk-avatar-stack > .blk-avatar")).toHaveLength(3);
});

test("a checkbox reports the state it moved to, so the caller holds the value and the box holds nothing", async () => {
  const changed = vi.fn();
  render(<Checkbox label="Notify me" checked={false} onCheckedChange={changed} />);
  await userEvent.click(screen.getByLabelText("Notify me"));
  expect(changed).toHaveBeenCalledWith(true);
});

test("indeterminate reaches the input itself, so a select-all header reads as partial to a screen reader", () => {
  const { container, rerender } = render(
    <Checkbox label="Select all" checked={false} indeterminate onCheckedChange={() => undefined} />,
  );
  const box = screen.getByLabelText("Select all") as HTMLInputElement;
  expect(box.indeterminate).toBe(true);
  expect(container.querySelector(".blk-checkbox")?.getAttribute("data-state")).toBe("indeterminate");
  rerender(<Checkbox label="Select all" checked onCheckedChange={() => undefined} />);
  expect((screen.getByLabelText("Select all") as HTMLInputElement).indeterminate).toBe(false);
  expect(container.querySelector(".blk-checkbox")?.getAttribute("data-state")).toBe("checked");
});

test("a disabled checkbox refuses the click, so an unavailable setting cannot be moved", async () => {
  const changed = vi.fn();
  render(<Checkbox label="Notify me" checked disabled onCheckedChange={changed} />);
  await userEvent.setup({ pointerEventsCheck: 0 }).click(screen.getByLabelText("Notify me"));
  expect(changed).not.toHaveBeenCalled();
});

test("the trigger opens the menu and the chosen act reaches the caller, so a row's acts are one component", async () => {
  const user = userEvent.setup();
  const removed = vi.fn();
  render(
    <Menu>
      <MenuTrigger className="blk-menu-trigger" aria-label="More">
        More
      </MenuTrigger>
      <MenuContent>
        <MenuItem>Rename</MenuItem>
        <MenuItem destructive onSelect={removed}>
          Delete
        </MenuItem>
      </MenuContent>
    </Menu>,
  );
  await user.click(screen.getByRole("button", { name: "More" }));
  const remove = await screen.findByRole("menuitem", { name: "Delete" });
  expect(remove.getAttribute("data-destructive")).toBe("true");
  await user.click(remove);
  expect(removed).toHaveBeenCalledOnce();
});

test("the menu button carries the class the trigger needs, so a menu is drawn without a bespoke button", async () => {
  const user = userEvent.setup();
  const chosen = vi.fn();
  const { container } = render(
    <>
      <Menu>
        <MenuButton>Board</MenuButton>
        <MenuContent>
          <MenuItem onSelect={chosen}>List</MenuItem>
        </MenuContent>
      </Menu>
      <Menu>
        <MenuButton icon label="More">
          <IconDots size={16} stroke={1.5} />
        </MenuButton>
        <MenuContent>
          <MenuItem>Rename</MenuItem>
        </MenuContent>
      </Menu>
    </>,
  );
  const labelled = screen.getByRole("button", { name: "Board" });
  expect(labelled.className).toBe("blk-menu-button");
  expect(labelled.querySelector("svg")).toBeTruthy();
  expect(labelled.getAttribute("data-icon")).toBeNull();

  const square = screen.getByRole("button", { name: "More" });
  expect(square.className).toBe("blk-menu-button");
  expect(square.getAttribute("data-icon")).toBe("true");
  expect(container.querySelectorAll(".blk-menu-button")).toHaveLength(2);

  await user.click(labelled);
  await user.click(await screen.findByRole("menuitem", { name: "List" }));
  expect(chosen).toHaveBeenCalledOnce();
});

test("onCloseAutoFocus reaches the panel, so the field a choice filled keeps the caret", async () => {
  const user = userEvent.setup();
  render(<ControlsMenuFocus />);
  await user.click(screen.getByRole("button", { name: "Insert" }));
  await user.click(await screen.findByRole("menuitem", { name: SNIPPET }));
  const field = screen.getByRole("textbox", { name: "Note" }) as HTMLInputElement;
  expect(field.value).toBe(SNIPPET);
  expect(document.activeElement).toBe(field);
});

test("the page carries every example, so the reference shows each primitive the blocks compose", () => {
  render(<ControlsPage />);
  for (const example of EXAMPLES) {
    expect(screen.getByRole("heading", { name: example })).toBeTruthy();
  }
});

test("the select-all box reads partial while some rows are picked, and takes or drops all of them", async () => {
  const user = userEvent.setup();
  const { container } = render(<ControlsStatesInteractive />);
  const all = screen.getByLabelText("Select every task") as HTMLInputElement;
  expect(all.indeterminate).toBe(false);
  expect(all.checked).toBe(false);

  await user.click(screen.getByLabelText("Draft the migration plan"));
  expect((screen.getByLabelText("Select every task") as HTMLInputElement).indeterminate).toBe(true);
  expect(container.querySelectorAll('.blk-item[data-selected="true"]')).toHaveLength(1);

  await user.click(screen.getByLabelText("Select every task"));
  expect(container.querySelectorAll('.blk-item[data-selected="true"]')).toHaveLength(3);
  expect((screen.getByLabelText("Select every task") as HTMLInputElement).checked).toBe(true);

  await user.click(screen.getByLabelText("Select every task"));
  expect(container.querySelectorAll('.blk-item[data-selected="true"]')).toHaveLength(0);
});

// The tick svg carries opacity, which makes it a stacking context painted over the transparent input,
// so a click at the centre of the box reached the svg and toggled nothing.
test("the drawn box takes no pointer, so a click anywhere on it reaches the input under it", () => {
  const rule = CHECKBOX_CSS.split(".blk-checkbox-box {")[1].split("}")[0];
  expect(rule).toContain("pointer-events: none");
});
