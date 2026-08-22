import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";

import {
  AGENT,
  MEMBER,
  NO_RUNS,
  SITE_KIND,
  TASK_KIND,
  TRIGGER_KIND,
  json,
  objectIndex,
  useStreamFake,
  viewCard,
} from "./harness";

const ARTIFACT = {
  id: "a1",
  filename: "notes.txt",
  subject: "notes",
  media_type: "text/plain",
  size_bytes: 12,
  created_at: "2026-07-31T09:00:00",
  url: "/dl/notes.txt",
};

beforeEach(() => {
  document.body.innerHTML = "";
  useStreamFake();
});

function serve() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/workspace/artifacts")) return json({ artifacts: [ARTIFACT] });
      if (url.includes("/workspace/radar")) return json({ runs: [] });
      if (url.includes("/objects/site")) return objectIndex(SITE_KIND, []);
      if (url.includes("/objects/scheduled_task")) return objectIndex(TASK_KIND, []);
      if (url.includes("/objects/source_trigger")) return objectIndex(TRIGGER_KIND, []);
      if (url.includes("/workspace/team")) return json({ members: [], can_add: false, domain: null });
      if (url.includes("/api/chats")) return json({ chats: [] });
      if (url.includes("/transcript")) return json({ messages: [] });
      return new Response("file body");
    }),
  );
}

test("the artifact viewer is torn down when the member navigates to another view", async () => {
  serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(screen.getByRole("button", { name: "Artifacts" }));
  await userEvent.click(await viewCard("notes.txt"));
  expect(await screen.findByText("file body")).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Radar" }));

  await waitFor(() => expect(screen.queryByText("file body")).toBeNull());
  expect(screen.queryByRole("button", { name: /^Close/ })).toBeNull();
  expect(await screen.findByText(NO_RUNS)).toBeTruthy();
});

test("the artifact viewer is torn down when the member returns to a conversation", async () => {
  serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(screen.getByRole("button", { name: "Artifacts" }));
  await userEvent.click(await viewCard("notes.txt"));
  expect(await screen.findByText("file body")).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "New conversation" }));

  await waitFor(() => expect(screen.queryByText("file body")).toBeNull());
  expect(screen.queryByRole("button", { name: /^Close/ })).toBeNull();
  expect(screen.getByLabelText("Message the app")).toBeTruthy();
});
