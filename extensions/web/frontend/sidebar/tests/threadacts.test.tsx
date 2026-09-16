import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";

import {
  AGENT,
  CHAT_ROW,
  chatsOnWire,
  CONVO_ID,
  json,
  MEMBER,
  useStreamFake,
  wire,
} from "../../tests/harness";

beforeEach(() => {
  useStreamFake();
});

/** Radix arms a 300ms timer either side of a hover, and vitest tears this file's environment down
 *  under a pending one. */
const CARD_DELAY_MS = 300;

afterEach(async () => {
  await new Promise((settled) => setTimeout(settled, CARD_DELAY_MS + 50));
});

const SLACK_LINK = "https://example.slack.com/archives/C1/p1700000000000001";

const SLACK_ROW = {
  ...CHAT_ROW,
  conversation_id: "66666666-6666-4666-8666-666666666666",
  title: "Deploy question",
  surface: "slack",
  surface_label: "#deploys",
  source: SLACK_LINK,
};

function writeText(): ReturnType<typeof vi.fn> {
  const written = vi.fn(async () => {});
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: written },
  });
  return written;
}

async function acts(title: string): Promise<HTMLElement> {
  const press = await screen.findByRole("button", { name: new RegExp(title) });
  const row = press.closest("li");
  if (!row) throw new Error(title + " stands in no row");
  return within(row).getByRole("button", { name: "Thread options" });
}

const FILING_ACTIONS = [
  "pin_conversation",
  "unpin_conversation",
  "archive_conversation",
  "unarchive_conversation",
  "delete_conversation",
];

/** The acts the conversation kind answers for, and what each one filed, the way the Home table's
 *  own wire reports them. */
function filingWire(posted: string[]) {
  return {
    ...chatsOnWire([CHAT_ROW]),
    "/actions/conversation/": (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        posted.push(url.split("/actions/conversation/")[1]);
        return json({ applied: true, message: "Saved." });
      }
      const conversationId = url.split("/actions/conversation/")[1].split("?")[0];
      return json({
        actions: FILING_ACTIONS.map((name) => ({
          name,
          description: "",
          input_schema: { properties: {} },
          call: { kind: "conversation", name: conversationId, action: name },
          label: name,
        })),
      });
    },
    "/transcript": () => json({ messages: [] }),
  };
}

test("a thread row holds its acts behind a mark drawn under the pointer and on the row's focus", async () => {
  wire({ ...chatsOnWire([CHAT_ROW]) });
  const view = render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const mark = await acts("Pick one thread");
  expect(mark.getAttribute("aria-haspopup")).toBe("menu");
  // The acts are revealed with the marks beside them, so the row's end is one thing that appears.
  const trail = mark.parentElement!.getAttribute("class")!;
  expect(trail).toContain("opacity-0");
  expect(trail).toContain("group-hover/row:opacity-100");
  expect(trail).toContain("group-has-[:focus-visible]/row:opacity-100");

  await userEvent.click(mark);
  expect(await screen.findByRole("menuitem", { name: "Copy link" })).toBeTruthy();
  expect(location.hash).toBe("");
  view.unmount();
});

test("a menu left open holds the row the pointer has gone from", async () => {
  wire({ ...chatsOnWire([CHAT_ROW]) });
  const view = render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const mark = await acts("Pick one thread");
  const row = mark.closest("li") as HTMLElement;
  const pill = row.querySelector("div") as HTMLElement;
  const line = within(row).getByText(CHAT_ROW.title).parentElement as HTMLElement;
  expect(line.className).not.toContain("me-6xl");
  expect(pill.className.split(" ")).not.toContain("bg-fill");

  await userEvent.click(mark);
  fireEvent.pointerLeave(row);

  expect(line.className).toContain("me-6xl");
  expect(pill.className.split(" ")).toContain("bg-fill");
  view.unmount();
});

test("copy link writes the thread's portal address", async () => {
  const written = writeText();
  wire({ ...chatsOnWire([CHAT_ROW]) });
  const view = render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await acts("Pick one thread"));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Copy link" }));

  expect(written).toHaveBeenCalledWith(location.origin + location.pathname + "#/c/" + CONVO_ID);
  view.unmount();
});

test("the thread menu carries the Home table's filing acts beside the link acts", async () => {
  const posted: string[] = [];
  wire(filingWire(posted));
  const view = render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await acts("Pick one thread"));
  const menu = await screen.findByRole("menu");
  expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
    "Archive",
    "Pin",
    "Delete",
    "Copy link",
  ]);

  await userEvent.click(within(menu).getByRole("menuitem", { name: "Pin" }));

  await vi.waitFor(() => expect(posted).toEqual([CONVO_ID + "/pin_conversation"]));
  view.unmount();
});

test("deleting a thread from the rail asks first and posts the verb the dialog answers", async () => {
  const posted: string[] = [];
  wire(filingWire(posted));
  const view = render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await acts("Pick one thread"));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Delete" }));
  expect(posted).toEqual([]);

  await userEvent.click(
    within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete" }),
  );

  await vi.waitFor(() => expect(posted).toEqual([CONVO_ID + "/delete_conversation"]));
  view.unmount();
});

test("a Slack thread leads back out to Slack and a portal thread does not", async () => {
  localStorage.setItem("rail-shown", "slack");
  wire({ ...chatsOnWire([CHAT_ROW, SLACK_ROW]) });
  const view = render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await acts("Pick one thread"));
  expect(screen.queryByRole("menuitem", { name: /^Open in / })).toBeNull();
  await userEvent.keyboard("{Escape}");

  await userEvent.click(await acts("Deploy question"));
  const away = await screen.findByRole("menuitem", { name: "Open in #deploys" });
  expect(away.getAttribute("href")).toBe(SLACK_LINK);
  expect(away.getAttribute("rel")).toBe("noopener noreferrer");
  view.unmount();
});
