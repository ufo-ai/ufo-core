import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect, useState } from "react";
import { expect, test } from "vitest";

import { useBeside } from "@/kernel/beside";
import { Pane, RecordPanel } from "@/kernel/pane";

function Maker({ verb }: { verb: string }) {
  const [making, setMaking] = useState(false);
  const beside = useBeside(
    making ? (
      <RecordPanel title={verb} onClose={() => setMaking(false)}>
        <p>{verb} form</p>
      </RecordPanel>
    ) : null,
    () => setMaking(false),
  );
  return (
    <>
      <button type="button" onClick={() => setMaking(true)}>
        {verb}
      </button>
      {beside}
    </>
  );
}

function Record({ title, mounts }: { title: string; mounts: string[] }) {
  useEffect(() => {
    mounts.push("mount:" + title);
    return () => void mounts.push("unmount:" + title);
  }, [title, mounts]);
  return (
    <RecordPanel title={title} onClose={() => {}}>
      <p>{title} body</p>
    </RecordPanel>
  );
}

function Held({ title, mounts }: { title: string; mounts: string[] }) {
  return useBeside(<Record title={title} mounts={mounts} />);
}

test("an act on a list opens one record beside it, and closing gives the width back", async () => {
  render(
    <Pane>
      <Maker verb="New thing" />
    </Pane>,
  );

  await userEvent.click(screen.getByRole("button", { name: "New thing" }));

  expect(screen.getAllByRole("complementary")).toHaveLength(1);
  expect(screen.getByRole("complementary", { name: "New thing" })).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Close" }));

  expect(screen.queryByRole("complementary")).toBeNull();
});

/** The column holds one record, so an act raised on a list that already has a record open beside it
 *  takes the column rather than landing under the table in a slot sized for one. */
test("a second record takes the column rather than standing under the list", async () => {
  render(
    <Pane>
      <Maker verb="New agent" />
      <Held title="assistant" mounts={[]} />
    </Pane>,
  );

  expect(screen.getByRole("complementary", { name: "assistant" })).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "New agent" }));

  const open = screen.getAllByRole("complementary");
  expect(open.map((panel) => panel.getAttribute("aria-label"))).toEqual(["New agent"]);

  await userEvent.click(screen.getByRole("button", { name: "Close" }));

  expect(screen.getByRole("complementary", { name: "assistant" })).toBeTruthy();
});

/** An act raised from inside a record cannot be drawn in the column that record is standing in:
 *  filling the column empties it of the record, which unmounts the tab that raised the act and
 *  takes the act with it. It lies over instead, and what it covers is inert. */
test("an act raised inside a record lies over it, and the record it covers is inert", async () => {
  render(
    <Pane>
      <p>the list</p>
      <RecordPanel title="assistant" onClose={() => {}}>
        <Maker verb="New scheduled task" />
      </RecordPanel>
    </Pane>,
  );

  await userEvent.click(screen.getByRole("button", { name: "New scheduled task" }));

  expect(screen.getAllByRole("complementary").map((p) => p.getAttribute("aria-label"))).toEqual([
    "assistant",
    "New scheduled task",
  ]);
  expect(screen.getByText("New scheduled task form")).toBeTruthy();
  expect(screen.getByText("the list")).toBeTruthy();
  expect(screen.getByRole("button", { name: "New scheduled task" }).closest("[inert]")).toBeTruthy();
});

/** A record that reached the column through a portal must not have stood inline first: an element
 *  that moves position is torn down and built again, firing every read inside it twice. */
test("a record mounts once on its way into the column", () => {
  const mounts: string[] = [];
  render(
    <Pane>
      <Held title="assistant" mounts={mounts} />
    </Pane>,
  );

  expect(mounts).toEqual(["mount:assistant"]);
});

test("Escape leaves a record, and focus goes back where it came from", async () => {
  render(
    <Pane>
      <Maker verb="New thing" />
    </Pane>,
  );

  const act = screen.getByRole("button", { name: "New thing" });
  await userEvent.click(act);
  const panel = screen.getByRole("complementary", { name: "New thing" });
  expect(panel.contains(document.activeElement)).toBe(true);

  await userEvent.keyboard("{Escape}");

  expect(screen.queryByRole("complementary")).toBeNull();
  expect(document.activeElement).toBe(act);
});

/** A record taking the column closes the form it displaced, rather than holding it below and
 *  refusing to give it back: the act that raised the form is still on the list beside the record,
 *  and a member who presses it again must get a form. */
test("a create act still opens after a record has taken the column from it", async () => {
  function Screen() {
    const [record, setRecord] = useState(false);
    return (
      <Pane>
        <Maker verb="New agent" />
        <button type="button" onClick={() => setRecord(true)}>
          open assistant
        </button>
        {record ? <Held title="assistant" mounts={[]} /> : null}
      </Pane>
    );
  }
  render(<Screen />);

  await userEvent.click(screen.getByRole("button", { name: "New agent" }));
  expect(screen.getByRole("complementary", { name: "New agent" })).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "open assistant" }));
  expect(screen.getByRole("complementary", { name: "assistant" })).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "New agent" }));

  expect(screen.getByRole("complementary", { name: "New agent" })).toBeTruthy();
});

/** A record the column stops showing is hidden, not torn down. Rebuilding it would take focus off
 *  the act the member just pressed and put it on a panel they did not open, and would send every
 *  read inside it a second time. */
test("closing a form hands focus back to the act, not to the record beneath it", async () => {
  const mounts: string[] = [];
  render(
    <Pane>
      <Maker verb="New agent" />
      <Held title="assistant" mounts={mounts} />
    </Pane>,
  );

  const act = screen.getByRole("button", { name: "New agent" });
  await userEvent.click(act);
  await userEvent.click(screen.getByRole("button", { name: "Close" }));

  expect(document.activeElement).toBe(act);
  expect(screen.getByRole("complementary", { name: "assistant" })).toBeTruthy();
  expect(mounts).toEqual(["mount:assistant"]);
});

/** A pane may host two of these. The displaced one unmounts a commit after its replacement has
 *  already taken focus, so a panel that hands focus back unconditionally takes it out of the panel
 *  the member just opened and drops them behind it. */
test("a displaced record leaves focus in the panel that displaced it", async () => {
  render(
    <Pane>
      <Maker verb="New thing" />
      <Maker verb="Other thing" />
    </Pane>,
  );

  await userEvent.click(screen.getByRole("button", { name: "New thing" }));
  expect(
    screen.getByRole("complementary", { name: "New thing" }).contains(document.activeElement),
  ).toBe(true);

  await userEvent.click(screen.getByRole("button", { name: "Other thing" }));

  const second = screen.getByRole("complementary", { name: "Other thing" });
  expect(screen.queryByRole("complementary", { name: "New thing" })).toBeNull();
  expect(second.contains(document.activeElement)).toBe(true);
});
