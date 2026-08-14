import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

import { useState } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Facts } from "@/components/ui/facts";
import { Field, FieldGroup, Input } from "@/components/ui/field";
import { Reveal } from "@/components/ui/reveal";
import { SILENT, Toast } from "@/components/ui/toast";

const VARIANTS = ["send", "outline", "row", "option"] as const;

test("a disabled control is dimmed and deaf to the pointer, whichever variant drew it", () => {
  for (const variant of VARIANTS) {
    const classes = buttonVariants({ variant });
    expect(classes).toContain("disabled:pointer-events-none");
    expect(classes).toContain("disabled:opacity-(--disabled)");
  }
});

test("a disabled control does not fire the act it is showing", async () => {
  const act = vi.fn();
  render(
    <Button variant="row" disabled onClick={act}>
      Set
    </Button>,
  );
  await userEvent.click(screen.getByRole("button", { name: "Set" }), { pointerEventsCheck: 0 });
  expect(act).not.toHaveBeenCalled();
});

test("a busy control stays focusable so the keyboard does not lose its place mid-act", async () => {
  render(
    <Button variant="send" busy>
      Add member
    </Button>,
  );
  const button = screen.getByRole("button", { name: "Add member" });
  expect(button.getAttribute("aria-disabled")).toBe("true");
  expect(button.hasAttribute("disabled")).toBe(false);
  await userEvent.tab();
  expect(document.activeElement).toBe(button);
});

test("a busy control swallows the click, so the act it is showing runs once", async () => {
  const act = vi.fn();
  render(
    <Button variant="send" busy onClick={act}>
      Add member
    </Button>,
  );
  await userEvent.click(screen.getByRole("button", { name: "Add member" }));
  expect(act).not.toHaveBeenCalled();
});

test("a busy submit control does not submit the form it sits in", async () => {
  const sent = vi.fn();
  render(
    <form onSubmit={sent}>
      <Button type="submit" variant="send" busy>
        Add member
      </Button>
    </form>,
  );
  await userEvent.click(screen.getByRole("button", { name: "Add member" }));
  expect(sent).not.toHaveBeenCalled();
});

test("a busy control reads as working, and still reads as working without motion", () => {
  render(
    <Button variant="send" busy>
      Add member
    </Button>,
  );
  const classes = screen.getByRole("button", { name: "Add member" }).className;
  expect(classes).toContain("animate-working");
  expect(classes).toContain("motion-reduce:animate-none");
  expect(classes).toContain("opacity-(--opacity-muted)");
});

test("a control that is not busy carries no busy marking", () => {
  render(<Button variant="send">Add member</Button>);
  const button = screen.getByRole("button", { name: "Add member" });
  expect(button.getAttribute("aria-disabled")).toBeNull();
  expect(button.className).not.toContain("animate-working");
});

test("the send variant keeps a boundary so it holds its height beside a field", () => {
  expect(buttonVariants({ variant: "send" })).toContain("border border-transparent");
});

test("a picked option is the filled chip, so it never reads lighter than a hovered neighbour", () => {
  const classes = buttonVariants({ variant: "option" });
  expect(classes).toContain("hover:bg-fill-hover");
  expect(classes).toContain("aria-pressed:bg-ink");
  expect(classes).toContain("aria-pressed:text-surface");
  expect(classes).toContain("aria-pressed:border-ink");
  expect(classes).not.toMatch(/aria-pressed:bg-fill-/);
});

test("a picked option holds that fill under the pointer and dims the way send does", () => {
  const classes = buttonVariants({ variant: "option" });
  expect(classes).toContain("aria-pressed:hover:bg-ink");
  expect(classes).toContain("aria-pressed:hover:opacity-(--opacity-muted-soft)");
  expect(buttonVariants({ variant: "send" })).toContain("hover:opacity-(--opacity-muted-soft)");
});

test("a dialog names itself, dims the surface behind it, and offers a way out", () => {
  render(
    <Dialog open>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add Member</DialogTitle>
          <DialogDescription>The address must be at example.com.</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="send">Add</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>,
  );
  const dialog = screen.getByRole("dialog", { name: "Add Member" });
  expect(dialog.textContent).toContain("The address must be at example.com.");
  expect(document.querySelector(".bg-scrim")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
});

test("a dialog's two footer controls measure the same, so neither reads as the lesser way out", () => {
  render(
    <Dialog open>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add Members</DialogTitle>
          <DialogDescription>The member is added to this workspace.</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="send">Add members</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>,
  );
  const padding = (name: string) =>
    screen
      .getByRole("button", { name })
      .className.split(" ")
      .filter((token) => token.startsWith("px-") || token.startsWith("py-"))
      .sort();
  expect(padding("Cancel")).toEqual(padding("Add members"));
});

test("a toast states what applied and then takes itself away", async () => {
  vi.useFakeTimers();
  const done = vi.fn();
  render(<Toast state={{ title: "2 members added." }} onDone={done} />);
  expect(screen.getByRole("status").textContent).toBe("2 members added.");
  await act(async () => {
    vi.runAllTimers();
  });
  expect(done).toHaveBeenCalled();
  vi.useRealTimers();
});

test("a toast with nothing to say draws nothing", () => {
  render(<Toast state={SILENT} onDone={vi.fn()} />);
  expect(screen.queryByRole("status")).toBeNull();
});

test("a toast states why under what", () => {
  const state = {
    title: "release-notes did not open.",
    description: "The skill directory answered 502.",
  };
  render(<Toast state={state} onDone={vi.fn()} />);
  const toast = screen.getByRole("status");
  expect(toast.textContent).toContain("release-notes did not open.");
  expect(toast.textContent).toContain("The skill directory answered 502.");
});

test("a dialog's cancel leaves without firing the act", async () => {
  const act = vi.fn();
  function Host() {
    const [open, setOpen] = useState(true);
    return (
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add Member</DialogTitle>
            <DialogDescription>The member is added to this workspace.</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="send" onClick={act}>
              Add
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    );
  }
  render(<Host />);
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(act).not.toHaveBeenCalled();
});

/** jsdom lays nothing out, so the fold — the one measurement `Reveal` reads — is stated here. */
function laid(content: number, fold: number) {
  const held = [
    vi.spyOn(Element.prototype, "scrollHeight", "get").mockReturnValue(content),
    vi.spyOn(Element.prototype, "clientHeight", "get").mockReturnValue(fold),
  ];
  return () => held.forEach((spy) => spy.mockRestore());
}

test("content past the fold is held there, and one control opens the whole of it", async () => {
  const restore = laid(900, 280);
  render(
    <Reveal>
      <p>a prompt longer than the fold</p>
    </Reveal>,
  );
  const more = screen.getByRole("button", { name: "Show more" });
  const region = document.getElementById(String(more.getAttribute("aria-controls")));
  expect(more.getAttribute("aria-expanded")).toBe("false");
  expect(region?.className).toContain("max-h-(--size-reveal)");

  await userEvent.click(more);
  const less = screen.getByRole("button", { name: "Show less" });
  expect(less.getAttribute("aria-expanded")).toBe("true");
  expect(region?.className).not.toContain("max-h-(--size-reveal)");
  restore();
});

test("content that already fits is offered no control that would do nothing", () => {
  const restore = laid(120, 280);
  render(
    <Reveal>
      <p>be useful</p>
    </Reveal>,
  );
  expect(screen.queryByRole("button")).toBeNull();
  expect(screen.getByText("be useful")).toBeTruthy();
  restore();
});

test("facts are read down a column, each labelled beside its own value", () => {
  render(
    <Facts
      rows={[
        { label: "Model", value: "claude-opus-5" },
        { label: "Round limit", value: "50" },
      ]}
    />,
  );
  const terms = [...document.querySelectorAll("dt")].map((node) => node.textContent);
  expect(terms).toEqual(["Model", "Round limit"]);
  expect(screen.getByText("Model").nextElementSibling?.textContent).toBe("claude-opus-5");
});

test("a form's submit sits on the card's own footer, under the last field", () => {
  render(
    <FieldGroup submit={<button type="submit">Save</button>}>
      <Field label="Model" htmlFor="model">
        <Input id="model" />
      </Field>
    </FieldGroup>,
  );
  const box = screen.getByLabelText("Model");
  const card = box.closest("form");
  expect(card?.className).toContain("border-edge");
  const footer = card?.lastElementChild;
  expect(footer?.contains(screen.getByText("Save"))).toBe(true);
  expect(footer?.contains(box)).toBe(false);
  expect(footer?.className).toContain("border-t");
});
