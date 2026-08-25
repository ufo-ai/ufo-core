import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { AppConversations } from "@/apps/bands";
import type { Placement } from "@/kernel/pager";
import { TooltipProvider } from "@/components/ui/tooltip";

import { AGENT_ID, json, wire } from "./harness";

beforeEach(() => {
  vi.unstubAllGlobals();
});

test("the work band draws the newest conversations, and says where there are more", async () => {
  /** The whole set is the conversations screen's answer, not this one — a band that paged would be
   *  a second listing with its own state in a page whose only channel for state is the place. So it
   *  draws the newest few, newest first, and says so rather than stopping in silence. */
  const rows = Array.from({ length: 12 }, (_, index) => ({
    name: `0000000${index}-0000-4000-8000-00000000000${index}`,
    summary: `Brief ${index}`,
    surface: "web",
    last_at: "2026-08-24T09:00:00",
  }));
  wire({ "/objects/conversation$": () => json({ objects: rows, next_cursor: null }) });
  const placed: Placement[] = [];
  render(
    <TooltipProvider>
      <AppConversations
        agentId={AGENT_ID}
        title="Conversations"
        blank="Nothing yet."
        place={{}}
        onPlace={(place) => placed.push(place)}
      />
    </TooltipProvider>,
  );
  // The row says its summary and, in the same breath, where the conversation came in and when.
  expect(await screen.findByText("Brief 0 Portal · Aug 24 2026")).toBeTruthy();
  expect(screen.queryByText(/Brief 8/)).toBeNull();
  expect(
    screen.getByText("The newest few. The conversations screen holds the rest."),
  ).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: /Brief 0/ }));
  expect(placed).toEqual([{ opens: [`object/${AGENT_ID}/conversation/${rows[0].name}`] }]);
});

test("the work band asks for the newest first, since a listing by id is no order at all", async () => {
  const { calls } = wire({
    "/objects/conversation$": () => json({ objects: [], next_cursor: null }),
  });
  render(
    <TooltipProvider>
      <AppConversations
        agentId={AGENT_ID}
        title="Conversations"
        blank="Nothing yet."
        place={{}}
        onPlace={() => {}}
      />
    </TooltipProvider>,
  );
  expect(await screen.findByText("Nothing yet.")).toBeTruthy();
  const read = calls.find((url) => url.includes("/objects/conversation"));
  expect(read).toContain("order_by=last_at");
  expect(read).toContain("order=desc");
});
