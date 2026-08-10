import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { MainAgentProvider } from "@/lib/mainAgent";
import { customizeHash } from "@/lib/route";

import {
  PlacedCustomize,
  AGENT,
  MEMBER,
  SECOND,
  SECOND_ID,
  json,
  opened,
  pick,
  useStreamFake,
  wire,
} from "./harness";

beforeEach(() => {
  useStreamFake();
});

const MINE = { name: "mine", description: "the first agent's skill", origin: "member" };
const THEIRS = { name: "theirs", description: "the second agent's skill", origin: "member" };

function skills() {
  return wire({
    "/skills": (url) => json({ skills: url.includes(SECOND_ID) ? [THEIRS] : [MINE] }),
    "/transcript": () => json({ messages: [] }),
  });
}

function placed() {
  return render(
    <MainAgentProvider agents={[AGENT, SECOND]}>
      <PlacedCustomize view="skills" />
    </MainAgentProvider>,
  );
}

test("the customize tab lists the main agent's skills and names the agents to pick", async () => {
  location.hash = "#/customize/skills";
  skills();
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} />);

  expect(await screen.findByText("the first agent's skill")).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Skills" }).getAttribute("aria-selected")).toBe("true");
  expect((await opened("Agent")).map((option) => option.textContent)).toEqual([
    "assistant",
    "second",
  ]);
});

test("picking an agent reads that agent's skills, names it in the hash, and Back returns", async () => {
  location.hash = "#/customize/skills";
  const { calls } = skills();
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} />);

  expect(await screen.findByText("the first agent's skill")).toBeTruthy();

  await pick("Agent", "second");

  expect(await screen.findByText("the second agent's skill")).toBeTruthy();
  expect(screen.queryByText("the first agent's skill")).toBeNull();
  expect(location.hash).toBe("#/customize/skills?agent=" + SECOND_ID);
  expect(calls.some((url) => url.includes("/agents/" + SECOND_ID + "/skills"))).toBe(true);

  history.back();
  await waitFor(() => expect(location.hash).toBe("#/customize/skills"));
  expect(await screen.findByText("the first agent's skill")).toBeTruthy();
});

test("a reloaded pick lands on that agent, which the empty line names", async () => {
  location.hash = customizeHash("skills", { agent: SECOND_ID });
  wire({ "/skills": () => json({ skills: [] }), "/transcript": () => json({ messages: [] }) });
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} />);

  expect(await screen.findByText("No skill has been saved onto second yet.")).toBeTruthy();
});

test("a save is admitted into the lane of the agent the member picked", async () => {
  const posted: string[] = [];
  location.hash = "#/customize/skills";
  wire({
    "/skills": () => json({ skills: [] }),
    "/intents": (url) => {
      posted.push(url);
      return json({ applied: true, message: "Saved." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} />);

  await pick("Agent", "second");
  await userEvent.click(await screen.findByRole("button", { name: "New skill" }));
  await userEvent.type(await screen.findByLabelText("Name"), "triage");
  await userEvent.type(screen.getByLabelText("Description"), "Load when triaging.");
  await userEvent.type(screen.getByLabelText("Instructions"), "steps");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toContain("/agents/" + SECOND_ID + "/intents");
});

test("a hash naming an agent this member cannot reach reports it", async () => {
  location.hash = customizeHash("skills", { agent: "99999999-9999-4999-8999-999999999999" });
  const { calls } = skills();
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} />);

  expect(await screen.findByText("No such agent.")).toBeTruthy();
  expect(calls.some((url) => url.includes("/skills"))).toBe(false);
});

test("picking an agent reads it once", async () => {
  location.hash = "#/customize/skills";
  const { calls } = skills();
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} />);

  expect(await screen.findByText("the first agent's skill")).toBeTruthy();
  await pick("Agent", "second");
  expect(await screen.findByText("the second agent's skill")).toBeTruthy();

  const reads = calls.filter((url) => url.includes("/skills"));
  expect(reads.filter((url) => url.includes(SECOND_ID)).length).toBe(1);
});

test("a workspace of one agent is offered no agent to pick", async () => {
  skills();
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedCustomize view="skills" />
    </MainAgentProvider>,
  );

  expect(await screen.findByText("the first agent's skill")).toBeTruthy();
  expect(screen.queryByRole("combobox", { name: "Agent" })).toBeNull();
});

test("the search and the origin filter reset with the agent they narrowed", async () => {
  skills();
  placed();

  const box = (await screen.findByRole("searchbox")) as HTMLInputElement;
  await userEvent.type(box, "nothing");
  expect(await screen.findByText("No skill matches this search.")).toBeTruthy();

  await pick("Agent", "second");

  expect(await screen.findByText("the second agent's skill")).toBeTruthy();
  expect(((await screen.findByRole("searchbox")) as HTMLInputElement).value).toBe("");
  expect(screen.getByRole("tab", { name: "All" }).getAttribute("aria-selected")).toBe("true");
});

test("the bar is drawn while the first read is still in flight", async () => {
  const pending = new Map<string, (value: Response) => void>();
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async (url: string) =>
        new Promise<Response>((resolve) => {
          pending.set(url, resolve);
        }),
    ),
  );
  placed();

  expect(await screen.findByRole("combobox", { name: "Agent" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "New skill" })).toBeTruthy();
  expect(pending.size).toBe(1);
});
