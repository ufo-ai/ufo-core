import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";

import { AGENT, CHAT_ROW, chatsOnWire, CONVO_ID, MEMBER, useStreamFake, wire } from "./harness";

beforeEach(() => {
  useStreamFake();
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

test("a thread row holds its acts behind a mark drawn under the pointer and on the row's focus", async () => {
  wire({ ...chatsOnWire([CHAT_ROW]) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const mark = await acts("Pick one thread");
  expect(mark.getAttribute("aria-haspopup")).toBe("menu");
  expect(mark.getAttribute("class")).toContain("opacity-0");
  expect(mark.getAttribute("class")).toContain("group-hover/row:opacity-100");
  expect(mark.getAttribute("class")).toContain("group-has-[:focus-visible]/row:opacity-100");

  await userEvent.click(mark);
  expect(await screen.findByRole("menuitem", { name: "Copy link" })).toBeTruthy();
  expect(location.hash).toBe("");
});

test("copy link writes the thread's portal address", async () => {
  const written = writeText();
  wire({ ...chatsOnWire([CHAT_ROW]) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await acts("Pick one thread"));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Copy link" }));

  expect(written).toHaveBeenCalledWith(location.origin + location.pathname + "#/c/" + CONVO_ID);
});

test("a Slack thread leads back out to Slack and a portal thread does not", async () => {
  localStorage.setItem("rail-shown", "slack");
  wire({ ...chatsOnWire([CHAT_ROW, SLACK_ROW]) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await acts("Pick one thread"));
  expect(screen.queryByRole("menuitem", { name: "Open in Slack" })).toBeNull();
  await userEvent.keyboard("{Escape}");

  await userEvent.click(await acts("Deploy question"));
  const away = await screen.findByRole("menuitem", { name: "Open in Slack" });
  expect(away.getAttribute("href")).toBe(SLACK_LINK);
  expect(away.getAttribute("rel")).toBe("noopener noreferrer");
});
