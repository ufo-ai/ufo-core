import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { IconPlus } from "@tabler/icons-react";
import { describe, expect, it, vi } from "vitest";

import {
  ActionBar,
  ActionBarActions,
  ActionBarCenter,
  ActionBarTitle,
  Composer,
  IconButton,
  Prompt,
  Prompts,
  SearchField,
} from "@/blocks/action-bar";
import { MenuContent, MenuItem } from "@/blocks/menu";
import { ActionBarPage } from "@/blocks/docs/ActionBarPage";
import { ComposerFocusExample } from "@/blocks/docs/examples/action-bar-composer-focus";
import { PromptsFooterExample } from "@/blocks/docs/examples/action-bar-prompts-footer";
import { ActionBarStatesInteractive } from "@/blocks/docs/examples/action-bar-states-interactive";
import { ActionBarTitleMenu } from "@/blocks/docs/examples/action-bar-title-menu";

const EXAMPLES = [
  "Header",
  "Header with actions",
  "Title menu",
  "Prompts",
  "Filter chips",
  "Prompts overflow",
  "Header with a centre line",
  "Toolbar",
  "Composer",
  "Composer two rows",
  "Composer outline",
  "Composer floating",
  "Composer focus",
  "Icon button states",
  "Prompts in a card footer",
  "States",
  "Every state",
  "Interactive states",
];

describe("action bar", () => {
  it("names an icon button for assistive technology", () => {
    render(
      <IconButton label="Search todos">
        <IconPlus size={16} stroke={1.5} />
      </IconButton>,
    );
    expect(screen.getByRole("button", { name: "Search todos" })).toBeTruthy();
  });

  it("calls a prompt back when it is pressed", async () => {
    const pressed = vi.fn();
    render(<Prompt onClick={pressed}>Summarize todos</Prompt>);
    await userEvent.click(screen.getByRole("button", { name: "Summarize todos" }));
    expect(pressed).toHaveBeenCalledTimes(1);
  });

  it("marks the chip the row is standing on, and reports it pressed", () => {
    const held = render(<Prompt active>#eng</Prompt>);
    const chip = screen.getByRole("button", { name: "#eng" });
    expect(chip.getAttribute("data-active")).toBe("true");
    expect(chip.getAttribute("aria-pressed")).toBe("true");
    held.unmount();
    render(<Prompt>#eng</Prompt>);
    const plain = screen.getByRole("button", { name: "#eng" });
    expect(plain.getAttribute("data-active")).toBeNull();
    expect(plain.getAttribute("aria-pressed")).toBeNull();
  });

  it("marks a pressed icon button apart from an active one, so a toggle reports its own state", () => {
    const { container } = render(
      <>
        <IconButton label="Priority" pressed>
          <IconPlus size={16} stroke={1.5} />
        </IconButton>
        <IconButton label="More" active>
          <IconPlus size={16} stroke={1.5} />
        </IconButton>
      </>,
    );
    const priority = screen.getByRole("button", { name: "Priority" });
    expect(priority.getAttribute("aria-pressed")).toBe("true");
    expect(priority.getAttribute("data-pressed")).toBe("true");
    expect(priority.getAttribute("data-active")).toBeNull();
    const more = screen.getByRole("button", { name: "More" });
    expect(more.getAttribute("aria-pressed")).toBeNull();
    expect(more.getAttribute("data-active")).toBe("true");
    expect(container.querySelectorAll('[data-pressed="true"]')).toHaveLength(1);
  });

  it("keeps the title a plain row when menu is the flag alone", () => {
    const { container } = render(<ActionBarTitle menu>Todos</ActionBarTitle>);
    const title = container.querySelector(".blk-action-bar-title")!;
    expect(title.tagName).toBe("DIV");
    expect(title.querySelector(".blk-action-bar-chevron")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("makes the whole title the menu trigger when menu is given a panel, keeping it the flex child", async () => {
    const chosen = vi.fn();
    const { container } = render(
      <ActionBar>
        <ActionBarTitle
          menu={
            <MenuContent align="start">
              <MenuItem onSelect={chosen}>Rename</MenuItem>
            </MenuContent>
          }
        >
          Todos
        </ActionBarTitle>
      </ActionBar>,
    );
    const trigger = screen.getByRole("button", { name: "Todos" });
    expect(trigger.className).toBe("blk-action-bar-title");
    expect(container.querySelector(".blk-action-bar > .blk-action-bar-title")).toBe(trigger);
    expect(trigger.querySelector(".blk-action-bar-chevron")).toBeTruthy();
    await userEvent.click(trigger);
    await userEvent.click(await screen.findByRole("menuitem", { name: "Rename" }));
    expect(chosen).toHaveBeenCalledOnce();
  });

  it("opens the lane's own menu from its title and reads the choice back into the name", async () => {
    const user = userEvent.setup();
    render(<ActionBarTitleMenu />);
    await user.click(screen.getByRole("button", { name: "Todos" }));
    await user.click(await screen.findByRole("menuitemcheckbox", { name: "Meetings" }));
    expect(screen.getByRole("button", { name: "Meetings" })).toBeTruthy();
  });

  it("takes the caret on autoFocus, and takes it back when focusKey moves but never on the first draw", () => {
    const focused = render(<Composer placeholder="Ask Assistant anything..." autoFocus />);
    expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Ask Assistant anything..." }));
    focused.unmount();

    const { rerender } = render(<Composer placeholder="Ask" focusKey={0} value="" />);
    const field = screen.getByRole("textbox", { name: "Ask" });
    expect(document.activeElement).not.toBe(field);
    rerender(<Composer placeholder="Ask" focusKey={1} value="Summarize today" />);
    expect(document.activeElement).toBe(field);
  });

  it("fills the composer from a chip and puts the caret back in the field", async () => {
    const user = userEvent.setup();
    render(<ComposerFocusExample />);
    await user.click(screen.getByRole("button", { name: "Summarize today" }));
    const field = screen.getByRole("textbox", { name: "Ask Assistant anything..." }) as HTMLInputElement;
    expect(field.value).toBe("Summarize today");
    expect(document.activeElement).toBe(field);
  });

  it("holds the centre line between the title and the actions", () => {
    const { container } = render(
      <ActionBar>
        <ActionBarTitle>Issues</ActionBarTitle>
        <ActionBarCenter>180 open issues</ActionBarCenter>
        <ActionBarActions>
          <IconButton label="More">
            <IconPlus size={16} stroke={1.5} />
          </IconButton>
        </ActionBarActions>
      </ActionBar>,
    );
    const parts = [...container.querySelectorAll(".blk-action-bar > *")].map((part) => part.className);
    expect(parts).toEqual(["blk-action-bar-title", "blk-action-bar-center", "blk-action-bar-actions"]);
    expect(screen.getByText("180 open issues").className).toBe("blk-action-bar-center");
  });

  it("hands the search field's value to its caller", async () => {
    const changed = vi.fn();
    render(<SearchField onChange={changed} />);
    await userEvent.type(screen.getByRole("searchbox", { name: "Search" }), "acme");
    expect(changed).toHaveBeenLastCalledWith("acme");
  });

  it("submits the composer on Enter and leaves the text in place", async () => {
    const submitted = vi.fn();
    render(<Composer placeholder="Ask Assistant anything..." onSubmit={submitted} />);
    const input = screen.getByRole("textbox", { name: "Ask Assistant anything..." });
    await userEvent.type(input, "draft the reply{Enter}");
    expect(submitted).toHaveBeenCalledWith("draft the reply");
    expect((input as HTMLInputElement).value).toBe("draft the reply");
  });

  it("submits the two-row composer on Enter and keeps Shift+Enter for a new line", async () => {
    const submitted = vi.fn();
    const { container } = render(
      <Composer rows={2} placeholder="Ask Assistant anything..." onSubmit={submitted} />,
    );
    const field = screen.getByRole("textbox", { name: "Ask Assistant anything..." });
    expect(field.tagName).toBe("TEXTAREA");
    expect(container.querySelector(".blk-composer")?.getAttribute("data-rows")).toBe("2");
    await userEvent.type(field, "draft the reply{Shift>}{Enter}{/Shift}");
    expect(submitted).not.toHaveBeenCalled();
    await userEvent.type(field, "{Enter}");
    expect(submitted).toHaveBeenCalledWith("draft the reply\n");
  });

  it("draws its own edge on a muted ground", () => {
    const muted = render(<Composer placeholder="Ask Assistant anything..." />);
    expect(muted.container.querySelector(".blk-composer")?.getAttribute("data-surface")).toBe("muted");
    muted.unmount();
    const outline = render(<Composer surface="outline" placeholder="Ask Assistant anything..." />);
    expect(outline.container.querySelector(".blk-composer")?.getAttribute("data-surface")).toBe("outline");
  });

  it("marks the floating composer, so the inverse pill is one attribute away", () => {
    const plain = render(<Composer placeholder="Ask Assistant anything..." />);
    expect(plain.container.querySelector(".blk-composer")?.getAttribute("data-floating")).toBeNull();
    plain.unmount();
    const floating = render(<Composer floating placeholder="Ask Assistant anything..." />);
    expect(floating.container.querySelector(".blk-composer")?.getAttribute("data-floating")).toBe("true");
  });

  it("shows the title chevron only for a menu", () => {
    const plain = render(<ActionBarTitle>Todos</ActionBarTitle>);
    expect(plain.container.querySelector(".blk-action-bar-chevron")).toBeNull();
    plain.unmount();
    const menu = render(<ActionBarTitle menu>Todos</ActionBarTitle>);
    expect(menu.container.querySelector(".blk-action-bar-chevron")).not.toBeNull();
  });

  it("wraps the chip row onto as many lines as it needs, and clips it otherwise", () => {
    const clipped = render(
      <Prompts>
        <Prompt>Open</Prompt>
      </Prompts>,
    );
    expect(clipped.container.querySelector(".blk-prompts")?.getAttribute("data-wrap")).toBeNull();
    clipped.unmount();
    const wrapped = render(
      <Prompts wrap>
        <Prompt>Open</Prompt>
      </Prompts>,
    );
    expect(wrapped.container.querySelector(".blk-prompts")?.getAttribute("data-wrap")).toBe("true");
  });

  it("wraps the chips a card footer holds, so the fade covers empty track and never a chip", () => {
    const { container } = render(<PromptsFooterExample />);
    const chips = container.querySelector(".blk-card-footer > .blk-prompts");
    expect(chips?.getAttribute("data-wrap")).toBe("true");
    expect(container.querySelectorAll(".blk-prompt")).toHaveLength(3);
  });

  it("refuses a disabled chip, so a filter with nothing behind it cannot be pressed", async () => {
    const pressed = vi.fn();
    render(
      <Prompt disabled onClick={pressed}>
        Archived
      </Prompt>,
    );
    const chip = screen.getByRole("button", { name: "Archived" });
    expect((chip as HTMLButtonElement).disabled).toBe(true);
    await userEvent.setup({ pointerEventsCheck: 0 }).click(chip);
    expect(pressed).not.toHaveBeenCalled();
  });

  it("refuses a disabled icon button, so an act with nothing behind it cannot be pressed", async () => {
    const pressed = vi.fn();
    render(
      <IconButton label="Add" disabled onClick={pressed}>
        <IconPlus size={16} stroke={1.5} />
      </IconButton>,
    );
    await userEvent.setup({ pointerEventsCheck: 0 }).click(screen.getByRole("button", { name: "Add" }));
    expect(pressed).not.toHaveBeenCalled();
  });

  it("clears the search field from the cross, which draws only while the value is not empty", async () => {
    const cleared = vi.fn();
    const empty = render(<SearchField value="" onChange={() => undefined} onClear={cleared} />);
    expect(screen.queryByRole("button", { name: "Clear" })).toBeNull();
    empty.unmount();
    render(<SearchField value="acme" onChange={() => undefined} onClear={cleared} />);
    await userEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(cleared).toHaveBeenCalledTimes(1);
  });

  it("a held composer refuses the send and says it is sending", async () => {
    const submitted = vi.fn();
    const { container } = render(
      <Composer placeholder="Ask Assistant anything..." value="draft the reply" busy onSubmit={submitted} />,
    );
    expect(container.querySelector(".blk-composer")?.getAttribute("data-busy")).toBe("true");
    expect(screen.getByText("Sending").className).toBe("blk-composer-busy");
    const input = screen.getByRole("textbox", { name: "Ask Assistant anything..." }) as HTMLInputElement;
    expect(input.disabled).toBe(true);
    fireEvent.submit(container.querySelector(".blk-composer")!);
    expect(submitted).not.toHaveBeenCalled();
  });

  it("a disabled composer refuses the field and the send", () => {
    const submitted = vi.fn();
    const { container } = render(
      <Composer placeholder="Ask Assistant anything..." disabled onSubmit={submitted} />,
    );
    expect(container.querySelector(".blk-composer")?.getAttribute("data-disabled")).toBe("true");
    expect((screen.getByRole("textbox", { name: "Ask Assistant anything..." }) as HTMLInputElement).disabled).toBe(
      true,
    );
    fireEvent.submit(container.querySelector(".blk-composer")!);
    expect(submitted).not.toHaveBeenCalled();
  });

  it("the interactive example filters, clears the search, wraps the chips and holds the composer", async () => {
    const user = userEvent.setup();
    const { container } = render(<ActionBarStatesInteractive />);

    await user.click(screen.getByRole("button", { name: "Blocked" }));
    expect(screen.getByRole("button", { name: "Blocked" }).getAttribute("data-active")).toBe("true");
    expect(screen.getByRole("button", { name: "Open" }).getAttribute("data-active")).toBeNull();

    await user.type(screen.getByRole("searchbox", { name: "Search" }), "plan");
    await user.click(screen.getByRole("button", { name: "Clear" }));
    expect((screen.getByRole("searchbox", { name: "Search" }) as HTMLInputElement).value).toBe("");

    await user.click(screen.getByRole("button", { name: "Wrap the chips" }));
    expect(container.querySelector(".blk-prompts")?.getAttribute("data-wrap")).toBe("true");

    await user.type(screen.getByRole("textbox", { name: "Ask Assistant anything..." }), "draft the reply{Enter}");
    expect(screen.getByText("Blocked: draft the reply")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Hold the composer" }));
    expect(screen.getByText("Sending")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
  });

  it("renders every example on the docs page", () => {
    render(<ActionBarPage />);
    for (const title of EXAMPLES) {
      expect(screen.getAllByRole("heading", { name: title }).length).toBeGreaterThan(0);
    }
  });
});
