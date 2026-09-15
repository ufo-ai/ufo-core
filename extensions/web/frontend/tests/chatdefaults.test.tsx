import { render, screen, waitFor } from "@testing-library/react";
import { expect, test } from "vitest";

import { MainAgentProvider } from "@/lib/mainAgent";

import {
  AGENT,
  AGENT_ID,
  CHAT_APP,
  CHAT_APP_ID,
  PlacedWorkspace,
  SETTINGS,
  json,
  pick,
  wire,
} from "./harness";

const CHAT_SETTINGS = {
  ...SETTINGS,
  spec: { ...SETTINGS.spec, sandbox_size: "small" },
  spec_schema: {
    properties: {
      ...SETTINGS.spec_schema.properties,
      reasoning: { type: "string", enum: ["low", "high"], title: "Thinking level" },
      sandbox_size: { type: "string", enum: ["small", "large"], title: "Sandbox size" },
    },
  },
};

function chatDefaults(agents = [AGENT]) {
  const posted: unknown[] = [];
  wire({
    "/settings": () => json(CHAT_SETTINGS),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Applied." });
    },
  });
  render(
    <MainAgentProvider agents={agents}>
      <PlacedWorkspace view="chat" />
    </MainAgentProvider>,
  );
  return posted;
}

test("the chat defaults tab picks a model and a thinking level and applies them to the chat app", async () => {
  const posted = chatDefaults();
  expect(await screen.findByLabelText("Thinking level")).toBeTruthy();
  expect(screen.getByLabelText("model")).toBeTruthy();
  expect(screen.getByLabelText("Sandbox size")).toBeTruthy();
  expect(screen.queryByLabelText("internet_access_allowed")).toBeNull();

  await pick("model", "Sonnet");
  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({
    verb: "apply",
    kind: "agent",
    name: "assistant",
    spec: { model: "sonnet", reasoning: "high" },
  });
  expect(await screen.findByText("Chat defaults saved.")).toBeTruthy();

  await pick("Thinking level", "Low");
  await waitFor(() => expect(posted.length).toBe(2));
  expect(posted[1]).toMatchObject({ spec: { model: "sonnet", reasoning: "low" } });
});

test("the chat defaults are the chat app's where the workspace holds one", async () => {
  const read: string[] = [];
  wire({
    "/settings": (url) => {
      read.push(String(url));
      return json(CHAT_SETTINGS);
    },
  });
  render(
    <MainAgentProvider agents={[AGENT, CHAT_APP]}>
      <PlacedWorkspace view="chat" />
    </MainAgentProvider>,
  );
  await screen.findByLabelText("Thinking level");
  expect(read.some((url) => url.includes(CHAT_APP_ID))).toBe(true);
  expect(read.some((url) => url.includes(AGENT_ID))).toBe(false);
});
