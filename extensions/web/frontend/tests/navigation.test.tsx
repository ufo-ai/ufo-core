import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";

import { AGENT, MEMBER, json, useStreamFake } from "./harness";

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
      if (url.includes("/workspace/sites")) return json({ available: false, sites: [] });
      if (url.includes("/workspace/team")) return json({ members: [], can_add: false, domain: null });
      if (url.includes("/api/chats")) return json({ chats: [] });
      if (url.includes("/transcript")) return json({ messages: [] });
      return new Response("file body");
    }),
  );
}

test("the artifact viewer is torn down when the member navigates to another view", async () => {
  serve();
  render(<App agents={[AGENT]} member={MEMBER} />);

  await userEvent.click(screen.getByRole("button", { name: "Workspace" }));
  await userEvent.click(await screen.findByRole("tab", { name: "Artifacts" }));
  await userEvent.click(await screen.findByRole("button", { name: "notes.txt" }));
  expect(await screen.findByText("file body")).toBeTruthy();

  await userEvent.click(screen.getByRole("tab", { name: "Sites" }));

  await waitFor(() => expect(screen.queryByText("file body")).toBeNull());
  expect(screen.queryByRole("button", { name: "Close" })).toBeNull();
  expect(await screen.findByText("No sites extension is installed.")).toBeTruthy();
});

test("the artifact viewer is torn down when the member returns to a conversation", async () => {
  serve();
  render(<App agents={[AGENT]} member={MEMBER} />);

  await userEvent.click(screen.getByRole("button", { name: "Workspace" }));
  await userEvent.click(await screen.findByRole("tab", { name: "Artifacts" }));
  await userEvent.click(await screen.findByRole("button", { name: "notes.txt" }));
  expect(await screen.findByText("file body")).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "New conversation" }));

  await waitFor(() => expect(screen.queryByText("file body")).toBeNull());
  expect(screen.queryByRole("button", { name: "Close" })).toBeNull();
  expect(screen.getByLabelText("Message the agent")).toBeTruthy();
});
