import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { expect, test } from "vitest";

import { TabPanel, TabStrip } from "@/kernel/tabs";

const TABS = ["overview", "tasks", "usage"] as const;

function Pane() {
  const [tab, setTab] = useState<(typeof TABS)[number]>("overview");
  return (
    <>
      <TabStrip
        group="pane"
        tabs={TABS}
        current={tab}
        label={(name) => name[0].toUpperCase() + name.slice(1)}
        onPick={setTab}
      />
      <TabPanel group="pane" current={tab}>
        {tab} body
      </TabPanel>
    </>
  );
}

test("a tab names the panel it controls, and the panel names the tab that labels it", () => {
  render(<Pane />);
  const tab = screen.getByRole("tab", { name: "Overview" });
  const panel = screen.getByRole("tabpanel");
  expect(tab.getAttribute("aria-controls")).toBe(panel.id);
  expect(panel.getAttribute("aria-labelledby")).toBe(tab.id);
});

test("only the selected tab is in the tab order, so one press reaches the strip", async () => {
  render(<Pane />);
  expect(screen.getByRole("tab", { name: "Overview" }).tabIndex).toBe(0);
  expect(screen.getByRole("tab", { name: "Tasks" }).tabIndex).toBe(-1);
  await userEvent.click(screen.getByRole("tab", { name: "Tasks" }));
  expect(screen.getByRole("tab", { name: "Overview" }).tabIndex).toBe(-1);
  expect(screen.getByRole("tab", { name: "Tasks" }).tabIndex).toBe(0);
});

test("an arrow moves the selection and carries the focus with it, wrapping at each end", async () => {
  render(<Pane />);
  screen.getByRole("tab", { name: "Overview" }).focus();
  await userEvent.keyboard("{ArrowRight}");
  expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Tasks" }));
  expect(screen.getByRole("tabpanel").textContent).toBe("tasks body");
  await userEvent.keyboard("{ArrowLeft}{ArrowLeft}");
  expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Usage" }));
  await userEvent.keyboard("{ArrowRight}");
  expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Overview" }));
});
