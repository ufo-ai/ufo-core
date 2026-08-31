import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import {
  refusedNotice,
  AGENT,
  MEMBER,
  SETTINGS,
  StreamFake,
  TURN_ID,
  json,
  openAgentSettings,
  useStreamFake,
  wire,
} from "./harness";

const ADMIN = { ...MEMBER, admin: true };

const SLOT = {
  name: "openai",
  slot: "OPENAI_API_KEY",
  extension: "models",
  description: "the key",
  filled: true,
};

const EMPTY_SLOT = {
  name: "datadog",
  slot: "DATADOG_API_KEY",
  extension: "coding",
  description: "paste the key from `api.datadoghq.com`",
  filled: false,
};

/** The credential collection's projected act, which the listing's Set and Replace address. */
const CREDENTIAL_ACTIONS = [
  {
    name: "request_credentials",
    description: "Ask the member for a credential through a private prompt.",
    input_schema: {
      properties: {
        reason: { type: "string", title: "Reason" },
        prompts: { type: "array", title: "Prompts" },
      },
      required: ["reason", "prompts"],
    },
    call: { kind: "credential", action: "request_credentials", input: {} },
    label: "Set credential",
  },
];

beforeEach(() => {
  useStreamFake();
});

test("a connect intent opens the stream for the turn it reports and shows the consent link", async () => {
  location.hash = "#/agents/" + AGENT.id;
  wire({
    "/connections": () => json({ connections: [] }),
    "/settings": () => json(SETTINGS),
    "/intents": () => json({ applied: true, message: "Requested.", turn_id: TURN_ID }),
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);
  await openAgentSettings("Assistant", "Connectors");

  await userEvent.click(await screen.findByRole("button", { name: "Add connector" }));
  await userEvent.type(
    await screen.findByLabelText("Provider"),
    "github",
  );
  await userEvent.click(screen.getByRole("button", { name: "Connect" }));

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  expect(StreamFake.last().url).toContain("/turns/" + TURN_ID + "/stream");

  StreamFake.last().emit("connect", { provider: "notion", label: "Notion", turn: TURN_ID });
  const link = await screen.findByRole("link", { name: "Open the provider consent page" });
  expect(link.getAttribute("href")).toBe("/surface/web/turns/" + TURN_ID + "/connect");
});

test("a credential intent that answers with a request renders the prompt carrying its seal", async () => {
  location.hash = "#/workspace/credentials";
  const posts: string[] = [];
  const intents: { url: string; body: unknown }[] = [];
  wire({
    "/workspace/credentials": () => json({ actions: CREDENTIAL_ACTIONS, slots: [SLOT] }),
    "/request_credentials": (url, init) => {
      intents.push({ url, body: JSON.parse(String(init?.body)) });
      return json({
        applied: true,
        message: "",
        turn_id: TURN_ID,
        credentials: {
          sealed: "seal-token",
          reason: "models authenticates with this value.",
          prompts: [{ slot: "OPENAI_API_KEY", prompt: "the key" }],
        },
      });
    },
    "/credentials": (_url, init) => {
      posts.push(String(init?.body));
      return json({ stored: true });
    },
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Replace" }));
  expect(await screen.findByText("models authenticates with this value.")).toBeTruthy();

  await userEvent.type(await screen.findByLabelText("the key"), "sk-live");
  await userEvent.click(screen.getByRole("button", { name: "Set credential" }));

  await waitFor(() => expect(posts.length).toBe(1));
  expect(intents[0]).toEqual({
    url: "/surface/web/agents/" + AGENT.id + "/actions/credential/request_credentials",
    body: {
      reason: "models authenticates with this value; it is stored encrypted and never shown again.",
      prompts: [{ slot: "OPENAI_API_KEY", prompt: "the key" }],
    },
  });
  const sent = new URLSearchParams(posts[0]);
  expect(sent.get("sealed")).toBe("seal-token");
  expect(sent.get("slot")).toBe("OPENAI_API_KEY");
  expect(sent.get("value")).toBe("sk-live");
});

/** The coding accounts a member holds are theirs, not the workspace's, so they are offered where a
 *  member comes back to replace one — above the workspace slots, under no heading of their own. */
test("the credentials screen offers the member their own coding accounts", async () => {
  location.hash = "#/workspace/credentials";
  wire({
    "/workspace/credentials": () => json({ slots: [SLOT] }),
    "/workspace/accounts": () =>
      json({
        accounts: [
          { provider: "openai", label: "ChatGPT", connected: true },
          { provider: "anthropic", label: "Claude", connected: false },
        ],
      }),
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  expect(
    await screen.findByText(
      "This account is yours alone. Each member connects their own, and it is used only for coding tasks.",
    ),
  ).toBeTruthy();
  const held = (await screen.findByRole("button", { name: "Disconnect" })).closest("div")!;
  expect(held.textContent).toContain("ChatGPT");
  expect(within(held).getByRole("button", { name: "Replace" })).toBeTruthy();
  expect(screen.getByText("Claude")).toBeTruthy();
  expect(
    [...document.querySelectorAll("main h2")].map((heading) => heading.textContent),
  ).toEqual(["Model providers"]);
});

test("credentials group, sort, and render slot state and literals", async () => {
  location.hash = "#/workspace/credentials";
  wire({
    "/workspace/credentials": () => json({ actions: CREDENTIAL_ACTIONS, slots: [SLOT, EMPTY_SLOT] }),
    "/workspace/accounts": () => json({ accounts: [] }),
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  expect(await screen.findByText("Credential values are shared across the workspace.")).toBeTruthy();
  expect(await screen.findByText("Filled", { selector: '[data-part="status"]' })).toBeTruthy();
  expect([...document.querySelectorAll("main h2")].map((heading) => heading.textContent)).toEqual([
    "Model providers",
    "Service keys",
  ]);
  const unset = screen.getByText("DATADOG_API_KEY").closest("li")!;
  expect(within(unset).getByRole("button", { name: "Set" })).toBeTruthy();
  expect(within(unset).queryByText("Filled")).toBeNull();
  expect(await screen.findByRole("tab", { name: "Not set" })).toBeTruthy();
  const code = screen.getByText("api.datadoghq.com");
  expect(code.tagName).toBe("CODE");
  expect(code.textContent).not.toContain("`");
  const cards = screen
    .getAllByRole("listitem")
    .filter((card) => card.querySelector("[data-part=primary]"));
  expect(cards.map((card) => card.querySelector("[data-part=primary]")?.textContent)).toEqual([
    "OPENAI_API_KEY",
    "DATADOG_API_KEY",
  ]);
});

/** A family name has to bind down to the cards it heads. Stacked at the band gap it stood the same
 *  distance from them as from the family above, and headed neither. */
test("a credential family stands further from the family above it than from its own cards", async () => {
  location.hash = "#/workspace/credentials";
  wire({ "/workspace/credentials": () => json({ actions: CREDENTIAL_ACTIONS, slots: [SLOT, EMPTY_SLOT] }) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await screen.findByText("Credential values are shared across the workspace.");
  const families = [...document.querySelectorAll("main h2")].map(
    (heading) => heading.closest("section")!,
  );
  expect(families).toHaveLength(2);
  const between = families[0].parentElement!;
  expect(between.className).toContain("gap-8xl");
  expect(families[0].className).toContain("gap-6xl");
});

/** A row's acts are a cluster, and the cluster holds one line. Three screens still draw one on a
 *  row — a set that wraps takes the row past the table's own pitch, which is the fault the
 *  connector and agent tables were carrying. */
test("a row's acts stand on one line, so the row keeps its pitch", async () => {
  location.hash = "#/workspace/credentials";
  wire({ "/workspace/credentials": () => json({ actions: CREDENTIAL_ACTIONS, slots: [SLOT, EMPTY_SLOT] }) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  const replace = await screen.findByRole("button", { name: "Replace" });
  const cluster = replace.parentElement!;
  expect(cluster.className).toContain("flex-nowrap");
  expect(cluster.contains(screen.getByRole("button", { name: "Clear" }))).toBe(true);
});

test("a stored credential states the slot it stored and re-reads the listing", async () => {
  location.hash = "#/workspace/credentials";
  let reads = 0;
  wire({
    "/workspace/credentials": () => {
      reads += 1;
      return json({ actions: CREDENTIAL_ACTIONS, slots: [SLOT] });
    },
    "/request_credentials": () =>
      json({
        applied: true,
        message: "",
        turn_id: TURN_ID,
        credentials: {
          reason: "models authenticates with this value.",
          sealed: "seal-token",
          prompts: [{ slot: "OPENAI_API_KEY", prompt: "the key" }],
        },
      }),
    "/credentials": () => json({ stored: true }),
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Replace" }));
  await userEvent.type(await screen.findByLabelText("the key"), "sk-live");
  await userEvent.click(screen.getByRole("button", { name: "Set credential" }));

  expect(await screen.findByText("Stored OPENAI_API_KEY.")).toBeTruthy();
  await waitFor(() => expect(reads).toBe(2));
});

test("a request whose second slot is refused keeps the first stored and asks only for the second", async () => {
  location.hash = "#/workspace/credentials";
  const posts: string[] = [];
  let writable = false;
  wire({
    "/workspace/credentials": () => json({ actions: CREDENTIAL_ACTIONS, slots: [SLOT] }),
    "/request_credentials": () =>
      json({
        applied: true,
        message: "",
        turn_id: TURN_ID,
        credentials: {
          reason: "models authenticates with these values.",
          sealed: "seal-token",
          prompts: [
            { slot: "OPENAI_API_KEY", prompt: "the key" },
            { slot: "OPENAI_ORG_ID", prompt: "the organization" },
          ],
        },
      }),
    "/credentials": (_url, init) => {
      const body = String(init?.body);
      posts.push(body);
      return body.includes("OPENAI_ORG_ID") && !writable
        ? new Response("that slot is not writable", { status: 400 })
        : json({ stored: true });
    },
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Replace" }));
  await userEvent.type(await screen.findByLabelText("the key"), "sk-live");
  await userEvent.type(screen.getByLabelText("the organization"), "org-1");
  await userEvent.click(screen.getByRole("button", { name: "Set credentials" }));

  expect(await screen.findByText("that slot is not writable")).toBeTruthy();
  expect(posts.length).toBe(2);
  expect(screen.queryByLabelText("the key")).toBeNull();
  expect(screen.getByRole("button", { name: "Set credential" })).toBeTruthy();

  writable = true;
  await userEvent.click(screen.getByRole("button", { name: "Set credential" }));

  expect(await screen.findByText("2 credentials stored.")).toBeTruthy();
  expect(posts.length).toBe(3);
});

test("a cleared credential states the outcome and re-reads the listing", async () => {
  location.hash = "#/workspace/credentials";
  let reads = 0;
  wire({
    "/workspace/credentials": () => {
      reads += 1;
      return json({ actions: CREDENTIAL_ACTIONS, slots: [SLOT] });
    },
    "/intents": () => json({ applied: true, message: "Cleared OPENAI_API_KEY." }),
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Clear" }));
  await userEvent.click(await screen.findByRole("button", { name: "Confirm clear" }));

  expect(await screen.findByText("Cleared OPENAI_API_KEY.")).toBeTruthy();
  await waitFor(() => expect(reads).toBe(2));
});

test("a destructive act posts nothing until a second click confirms it, and leaving it disarms", async () => {
  location.hash = "#/workspace/credentials";
  const intents: string[] = [];
  const refused = (_url: string, init?: RequestInit) => {
    intents.push(String(init?.body));
    return json({ applied: false, message: "Only an admin may clear it." });
  };
  wire({
    "/workspace/credentials": () => json({ actions: CREDENTIAL_ACTIONS, slots: [SLOT] }),
    "/intents": refused,
    "/request_credentials": refused,
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Clear" }));
  expect(intents.length).toBe(0);

  await userEvent.click(screen.getByRole("button", { name: "Replace" }));
  expect(screen.queryByRole("button", { name: "Confirm clear" })).toBe(null);
  await waitFor(() => expect(intents.length).toBe(1));

  await userEvent.click(screen.getByRole("button", { name: "Clear" }));
  await userEvent.click(screen.getByRole("button", { name: "Confirm clear" }));
  await waitFor(() => expect(intents.length).toBe(2));
  expect(JSON.parse(intents[1]).verb).toBe("delete");
});

test("two acts in a row each re-read, even though the server answers one constant message", async () => {
  location.hash = "#/workspace/credentials";
  let reads = 0;
  wire({
    "/workspace/credentials": () => {
      reads += 1;
      return json({ actions: CREDENTIAL_ACTIONS, slots: [SLOT, { ...SLOT, name: "notion", slot: "NOTION_TOKEN" }] });
    },
    "/intents": () => json({ applied: true, message: "Saved.", turn_id: TURN_ID }),
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  const clears = await screen.findAllByRole("button", { name: "Clear" });
  await userEvent.click(clears[0]);
  await userEvent.click(await screen.findByRole("button", { name: "Confirm clear" }));
  expect(await screen.findByText("Saved.")).toBeTruthy();
  await waitFor(() => expect(reads).toBe(2));

  const again = await screen.findAllByRole("button", { name: "Clear" });
  await userEvent.click(again[1]);
  await userEvent.click(await screen.findByRole("button", { name: "Confirm clear" }));
  await waitFor(() => expect(reads).toBe(3));
});

const REQUESTED = {
  applied: true,
  message: "",
  turn_id: TURN_ID,
  credentials: {
    reason: "models authenticates with this value.",
    sealed: "seal-token",
    prompts: [{ slot: "OPENAI_API_KEY", prompt: "the key" }],
  },
};

test("an empty slot offers Set, and a refused act states the refusal in place", async () => {
  location.hash = "#/workspace/credentials";
  wire({
    "/workspace/credentials": () => json({ actions: CREDENTIAL_ACTIONS, slots: [{ ...SLOT, filled: false }] }),
    "/request_credentials": () => json({ applied: false, message: "Only an admin may set it." }),
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Set" }));

  await refusedNotice("Only an admin may set it.");
  expect(screen.queryByRole("button", { name: "Clear" })).toBe(null);
});

test("a workspace with no declared slots says so", async () => {
  location.hash = "#/workspace/credentials";
  wire({ "/workspace/credentials": () => json({ actions: CREDENTIAL_ACTIONS, slots: [] }) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  expect(await screen.findByText("No credential slots are declared.")).toBeTruthy();
});

test("the secret field hides what a member types and refuses whitespace", async () => {
  location.hash = "#/workspace/credentials";
  const posts: string[] = [];
  wire({
    "/workspace/credentials": () => json({ actions: CREDENTIAL_ACTIONS, slots: [SLOT] }),
    "/request_credentials": () => json(REQUESTED),
    "/credentials": (_url, init) => {
      posts.push(String(init?.body));
      return json({ stored: true });
    },
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Replace" }));
  const field = await screen.findByLabelText("the key");
  expect(field.getAttribute("type")).toBe("password");

  await userEvent.type(field, "   ");
  await userEvent.click(screen.getByRole("button", { name: "Set credential" }));
  expect(posts.length).toBe(0);
});

test("a refused store states the reason the server gave and keeps the field", async () => {
  location.hash = "#/workspace/credentials";
  wire({
    "/workspace/credentials": () => json({ actions: CREDENTIAL_ACTIONS, slots: [SLOT] }),
    "/request_credentials": () => json(REQUESTED),
    "/credentials": () => new Response("that seal has expired", { status: 400 }),
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Replace" }));
  await userEvent.type(await screen.findByLabelText("the key"), "sk-live");
  await userEvent.click(screen.getByRole("button", { name: "Set credential" }));

  expect(await screen.findByText("that seal has expired")).toBeTruthy();
  expect(screen.getByLabelText("the key")).toBeTruthy();
});

