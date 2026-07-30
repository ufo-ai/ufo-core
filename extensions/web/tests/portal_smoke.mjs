// A stub DOM stands in for the browser so the page's script runs whole under node: ids resolve
// only when the page's own markup carries them, insertBefore throws on a detached reference as
// the real DOM does, and a stub-level fault surfaces as a thrown error rather than a silently
// blank portal. Run: node portal_smoke.mjs <path-to-portal.html>.
import { readFileSync } from "node:fs";
import { argv, exit } from "node:process";

class StubElement {
  constructor(tag) {
    this.tagName = tag;
    this.childNodes = [];
    this.parentNode = null;
    this.listeners = {};
    this.attributes = {};
    this.dataset = {};
    this.style = {};
    this.className = "";
    this.textContent = "";
    this.title = "";
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.checked = false;
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.selectedOptions = [];
  }
  get children() {
    return this.childNodes;
  }
  get firstChild() {
    return this.childNodes[0] ?? null;
  }
  get isConnected() {
    let node = this;
    while (node.parentNode) node = node.parentNode;
    return node === documentRoot;
  }
  appendChild(child) {
    child.remove();
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }
  append(...nodes) {
    nodes.forEach((node) => this.appendChild(node));
  }
  createTHead() {
    return this.appendChild(new StubElement("thead"));
  }
  createTBody() {
    return this.appendChild(new StubElement("tbody"));
  }
  insertRow() {
    return this.appendChild(new StubElement("tr"));
  }
  insertCell() {
    return this.appendChild(new StubElement("td"));
  }
  prepend(...nodes) {
    for (const node of nodes.reverse()) {
      node.remove();
      node.parentNode = this;
      this.childNodes.unshift(node);
    }
  }
  insertBefore(node, reference) {
    const at = this.childNodes.indexOf(reference);
    if (at === -1) throw new Error("insertBefore: reference is not a child");
    node.remove();
    node.parentNode = this;
    this.childNodes.splice(at, 0, node);
    return node;
  }
  replaceChildren(...nodes) {
    this.childNodes.forEach((child) => {
      child.parentNode = null;
    });
    this.childNodes = [];
    nodes.forEach((node) => this.appendChild(node));
  }
  remove() {
    this.parentNode?.childNodes.splice(this.parentNode.childNodes.indexOf(this), 1);
    this.parentNode = null;
  }
  addEventListener(name, handler) {
    (this.listeners[name] ??= []).push(handler);
  }
  async fire(name, event = {}) {
    if (this.disabled) return;
    for (const handler of this.listeners[name] ?? []) {
      await handler({ preventDefault() {}, ...event });
    }
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
  focus() {}
  matches(selector) {
    if (selector.startsWith("#")) return this.attributes.id === selector.slice(1);
    if (selector.startsWith(".")) {
      const mine = this.className.split(" ");
      return selector.slice(1).split(".").every((part) => mine.includes(part));
    }
    const [head, attr] = selector.split("[");
    const [tag, cls] = head.split(".");
    if (tag && tag !== "*" && this.tagName !== tag) return false;
    if (cls && !this.className.split(" ").includes(cls)) return false;
    if (attr) {
      const name = attr.replace("]", "").split("=")[0];
      if (name.startsWith("data-")) return this.dataset[name.slice(5)] !== undefined;
      return this.attributes[name] !== undefined;
    }
    return true;
  }
  *walk() {
    for (const child of this.childNodes) {
      yield child;
      yield* child.walk();
    }
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] ?? null;
  }
  querySelectorAll(selector) {
    const found = [];
    for (const part of selector.split(",")) {
      for (const node of this.walk()) {
        if (node.matches(part.trim())) found.push(node);
      }
    }
    return found;
  }
  get id() {
    return this.attributes.id ?? "";
  }
  set id(value) {
    this.attributes.id = value;
  }
  set htmlFor(value) {
    this.attributes.for = value;
  }
  set type(value) {
    this.attributes.type = value;
  }
  get type() {
    return this.attributes.type ?? "";
  }
  set selected(value) {
    if (value) this.parentNode?.selectedOptions?.push(this);
  }
}

const documentRoot = new StubElement("html");
const body = new StubElement("body");
documentRoot.appendChild(body);

const html = readFileSync(argv[2], "utf8");
const idPattern = /id="([a-z-]+)"/g;
const pageIds = new Set([...html.matchAll(idPattern)].map(([, id]) => id));
const byId = {};
function element(tag, id) {
  const node = new StubElement(tag);
  if (id) {
    node.attributes.id = id;
    byId[id] = node;
  }
  return node;
}
const nav = element("nav");
const main = element("main");
const tokenCard = element("section", "token-card");
body.append(tokenCard, nav, main);
tokenCard.appendChild(element("form", "token-form"));
nav.append(element("div"), element("ul", "agents"), element("ul", "workspace"));
const footer = element("footer");
footer.append(element("span", "member-email"), element("a", "spend"), element("button", "admin"));
nav.appendChild(footer);
const composer = element("form", "composer");
const sendButton = element("button");
sendButton.className = "send";
composer.append(element("input", "files"), element("input", "msg"), sendButton);
main.append(
  element("div", "agent-head"),
  element("div", "tabs"),
  element("div", "log"),
  element("div", "panel"),
  composer,
);
for (const id of pageIds) {
  if (!(id in byId)) throw new Error(`page markup carries id "${id}" the stub does not build`);
}

const AGENT_A = "11111111-1111-4111-8111-111111111111";
const AGENT_B = "22222222-2222-4222-8222-222222222222";
const RICH = {
  turn_id: "t1",
  title: "Deploy plan",
  questions: [
    {
      question: "Which environments?",
      multi_select: true,
      options: [{ label: "Prod", description: "Production servers" }, { label: "Stage" }],
    },
    {
      header: "Schedule",
      question: "When should the deploy run?",
      options: [{ label: "Now", description: "Right away" }, { label: "Later" }],
    },
    {
      question: "Anything else?",
      free_text_only: true,
      options: [{ label: "Skip" }],
    },
    {
      question: "Share the logs?",
      allow_attachments: true,
      options: [{ label: "Attach" }],
    },
    {
      question: "Which region?",
      options: Array.from({ length: 11 }, (_, i) => ({ label: "r" + i })),
    },
    { question: "Anything not covered?" },
  ],
};
const LONE = {
  turn_id: "t9",
  title: "Release",
  questions: [{ question: "Ship it?", options: [{ label: "Ship" }, { label: "Hold" }] }],
};
const CREDENTIALS = {
  sealed: "sealed-blob",
  reason: "Connect ACME before the sync runs.",
  prompts: [{ slot: "api_key", prompt: "Acme API key" }],
};
const wire = {
  "/api/agents": {
    member: { email: "admin@example.com", admin: true },
    agents: [
      { id: AGENT_A, name: "assistant", main: true, model: "auto" },
      { id: AGENT_B, name: "ops", main: false, model: "claude-sonnet-5" },
    ],
  },
  answered: [],
  credentialPosts: [],
  "/api/admin": {
    agents: [
      {
        id: AGENT_A,
        name: "assistant",
        main: true,
        model: "auto",
        internet_access_allowed: true,
        installations: [],
        web_audience: [],
      },
      {
        id: AGENT_B,
        name: "ops",
        main: false,
        model: "claude-sonnet-5",
        internet_access_allowed: false,
        installations: [],
        web_audience: [],
      },
    ],
    members: [
      { id: "33333333-3333-4333-8333-333333333333", email: "admin@example.com", admin: true, seated: true },
      { id: "44444444-4444-4444-8444-444444444444", email: "m@example.com", admin: false, seated: true },
    ],
    models: ["auto", "claude-opus-4-8"],
    seats: { limit: null, included: null },
    caps: [
      {
        scope: "agent",
        subject: "assistant",
        window_seconds: 86400,
        limit_micro_usd: 5000000,
        on_breach: "park",
      },
    ],
    deploy: {
      sandbox_internet: false,
      extensions: [{ name: "web", version: "0.1.0", sandbox_internet: false }],
    },
  },
  posted: [],
  intentUrls: [],
  connectUrl: null,
  connections: {
    connections: [
      {
        provider: "github",
        account_id: "gh-acct",
        grant: "github-gh-acct-abcd1234",
        owner_email: "admin@example.com",
        shared: false,
        connected_at: "2026-07-01T08:30:00+00:00",
      },
      {
        provider: "slack",
        account_id: "sl-acct",
        grant: "slack-sl-acct-ef567890",
        owner_email: null,
        shared: true,
        connected_at: "2026-07-02T08:30:00+00:00",
      },
    ],
  },
  memoryQueries: [],
  memory: {
    available: true,
    recent: [
      {
        kind: "fact",
        text: "the launch codename is bluebird",
        ref: "memory/1",
        created_at: "2026-07-29T10:00:00+00:00",
      },
      {
        kind: "source",
        text: "the codename page",
        ref: "page/9",
        created_at: "2026-07-29T09:00:00+00:00",
      },
    ],
    searched: [
      {
        kind: "source",
        text: "the runway is painted",
        ref: null,
        created_at: "2026-07-28T09:00:00+00:00",
      },
    ],
  },
  workspace: {
    sources: {
      sources: [
        {
          backend: "gmail",
          owner_email: "admin@example.com",
          shared: false,
          consecutive_errors: 1,
          next_sync_at: "2026-07-30T13:00:00+00:00",
          name: "gmail-1a2b3c4d",
          stream: "messages",
          account_id: "acct-1",
          base_url: "",
        },
        {
          backend: "gmail",
          owner_email: "admin@example.com",
          shared: false,
          consecutive_errors: 0,
          next_sync_at: "2026-07-30T12:00:00+00:00",
          name: "gmail-1a2b3c4d",
          stream: "threads",
          account_id: "acct-1",
          base_url: "",
        },
        {
          backend: "folder",
          owner_email: null,
          shared: true,
          consecutive_errors: 0,
          next_sync_at: "2026-07-30T11:00:00+00:00",
          name: null,
          stream: null,
          account_id: null,
          base_url: null,
        },
      ],
    },
    credentials: {
      slots: [
        {
          slot: "api_key",
          name: "api-key",
          description: "Acme API key",
          extension: "acme",
          filled: false,
        },
        {
          slot: "signing_key",
          name: "signing-key",
          description: "Signing key",
          extension: "acme",
          filled: true,
        },
      ],
    },
    artifacts: {
      artifacts: [
        {
          filename: "report.pdf",
          subject: "the quarterly numbers",
          media_type: "application/pdf",
          size_bytes: 2048,
          created_at: "2026-07-29T11:00:00+00:00",
          url: "https://web/surface/web/artifacts/report.pdf?token=signed",
        },
      ],
    },
    sites: {
      available: true,
      sites: [{ name: "landing-ab12cd34", summary: "port 3000, shared" }],
    },
    usage: {
      window_seconds: 86400,
      total_micro_usd: 1500000,
      by_dimension: [{ dimension: "tokens", amount: 4200, priced_micro_usd: 1500000 }],
      caps: [{ window_seconds: 3600, limit_micro_usd: 5000000, on_breach: "park" }],
      workspace: {
        total_micro_usd: 9000000,
        by_dimension: [{ dimension: "egress", amount: 7, priced_micro_usd: 9000000 }],
        by_member: [{ label: "admin@example.com", priced_micro_usd: 9000000 }],
        by_agent: [{ label: "assistant", priced_micro_usd: 9000000 }],
      },
    },
  },
  overview: {
    agent: {
      name: "assistant",
      main: true,
      prompt: "be brief",
      prompt_digest: "digest",
      surfaces: [],
      updated_at: "2026-07-29T00:00:00+00:00",
    },
    deploy: { sandbox_internet: false },
    models: ["auto", "claude-opus-4-8"],
    spec: { model: "auto", internet_access_allowed: true },
    spec_schema: {
      properties: { model: { type: "string" }, internet_access_allowed: { type: "boolean" } },
    },
    audience: [],
  },
};

const sandbox = {
  document: {
    getElementById: (id) => (pageIds.has(id) ? byId[id] : null),
    querySelector: (selector) => body.querySelector(selector),
    createElement: (tag) => new StubElement(tag),
    createTextNode: (text) => {
      const node = new StubElement("#text");
      node.textContent = text;
      return node;
    },
    body,
  },
  location: { hash: "" },
  fetch: async (url, options) => {
    if (options?.method === "POST" && url.endsWith("/credentials")) {
      if (wire.credentialThrows) {
        wire.credentialThrows = false;
        throw new TypeError("network down");
      }
      wire.credentialPosts.push(String(options.body));
      if (wire.credentialRefuses) {
        wire.credentialRefuses = false;
        return { ok: false, status: 413, text: async () => "value too large" };
      }
      return { ok: true, status: 200, json: async () => ({ stored: "api_key" }) };
    }
    if (options?.method === "POST" && url.endsWith("/intents")) {
      const submitted = JSON.parse(options.body);
      wire.posted.push(submitted);
      wire.intentUrls.push(url);
      if (wire.holdPost) await wire.holdPost;
      wire.overview.agent.updated_at = "2026-07-30T12:00:00+00:00";
      if (submitted.verb === "request" && submitted.kind === "credential") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            applied: true,
            message: "",
            turn_id: "t",
            credentials: {
              sealed: "sealed-from-the-turn",
              reason: "Set api_key from the portal.",
              prompts: [{ slot: "api_key", prompt: "Acme API key" }],
            },
          }),
        };
      }
      if (wire.intentRefuses) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ applied: false, message: "Not applied.", turn_id: "t" }),
        };
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({ applied: true, message: "Saved.", turn_id: "t" }),
      };
    }
    if (options?.method === "POST") {
      if (!url.endsWith("/chat")) throw new Error(`unstubbed POST: ${url}`);
      wire.answered.push({ url, body: options.body, headers: options.headers });
      if (wire.holdAnswer) await wire.holdAnswer;
      return {
        ok: true,
        status: 200,
        json: async () => ({ turn_id: "answered-turn", body: "server:" + options.body }),
      };
    }
    if (url.endsWith("/api/agents") && wire.signedOut) {
      return { ok: false, status: 401, json: async () => ({}) };
    }
    if (url.endsWith("/skills")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          skills: [
            { name: "release-notes", description: "How release notes read.", origin: "member" },
            { name: "memory", description: "Recall and store.", origin: "deploy" },
          ],
        }),
      };
    }
    if (url.includes("/agents/") && url.endsWith("/usage")) {
      return { ok: false, status: 404, json: async () => ({}) };
    }
    if (url.endsWith("/connections")) {
      return { ok: true, status: 200, json: async () => structuredClone(wire.connections) };
    }
    if (url.endsWith("/overview") && wire.overviewThrows) throw new TypeError("network down");
    if (url.endsWith("/overview") && wire.overviewStatus) {
      return { ok: false, status: wire.overviewStatus, json: async () => ({}) };
    }
    if (url.endsWith(`/agents/${AGENT_A}/transcript`)) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ messages: [], question: structuredClone(RICH) }),
      };
    }
    if (url.endsWith(`/agents/${AGENT_B}/transcript`)) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          messages: [],
          question: structuredClone(LONE),
          credentials: structuredClone(CREDENTIALS),
          files: [{ filename: "r.pdf", size_bytes: 3 }],
        }),
      };
    }
    if (url.includes("/workspace/")) {
      const view = url.split("/workspace/")[1];
      if (wire.workspaceStatus) {
        return { ok: false, status: wire.workspaceStatus, json: async () => ({}) };
      }
      if (wire.workspaceThrows) {
        wire.workspaceThrows = false;
        throw new TypeError("network down");
      }
      if (view.startsWith("memory")) {
        const query = view.includes("?q=")
          ? decodeURIComponent(view.split("?q=")[1])
          : null;
        wire.memoryQueries.push(query);
        return {
          ok: true,
          status: 200,
          json: async () => ({
            available: wire.memory.available,
            matches: structuredClone(query ? wire.memory.searched : wire.memory.recent),
          }),
        };
      }
      const stubbed = wire.workspace[view];
      if (!stubbed) throw new Error(`unstubbed workspace fetch: ${url}`);
      return { ok: true, status: 200, json: async () => structuredClone(stubbed) };
    }
    const payload = url.endsWith("/overview")
      ? wire.overview
      : url.endsWith("/api/agents")
        ? wire["/api/agents"]
        : url.endsWith("/api/admin")
          ? wire["/api/admin"]
          : null;
    if (payload === null) throw new Error(`unstubbed fetch: ${url}`);
    return { ok: true, status: 200, json: async () => structuredClone(payload) };
  },
  EventSource: class {
    constructor() {
      this.handlers = {};
      setTimeout(() => {
        if (wire.connectUrl) {
          this.handlers.connect?.({ data: JSON.stringify({ url: wire.connectUrl }) });
        }
        this.handlers.terminal?.({
          data: JSON.stringify({ status: "done", text: "ok", model: "auto", tokens: 1, cost_micro_usd: 1 }),
        });
      }, 0);
    }
    addEventListener(name, handler) {
      this.handlers[name] = handler;
    }
    close() {}
  },
  console,
};

const script = html.split("<script>")[1].split("</script>")[0];
const run = new Function(...Object.keys(sandbox), `"use strict";\n${script}`);

const texts = (nodes) => nodes.map((node) => node.textContent);

try {
  run(...Object.values(sandbox));
  await new Promise((resolve) => setTimeout(resolve, 0));
  const agentButtons = byId.agents.querySelectorAll("button");
  if (agentButtons.length !== 2) throw new Error(`sidebar built ${agentButtons.length} agents`);
  const tabNamed = (name) =>
    byId.tabs.childNodes.find((node) => node.textContent === name)
    ?? (() => { throw new Error(`no ${name} tab`); })();
  const qrows = byId.log.querySelectorAll(".qrow");
  if (qrows.length !== 6) throw new Error(`question panel built ${qrows.length} rows`);
  const [multi, choice, free, attach, wide, bare] = qrows;
  const bareLines = texts(bare.querySelectorAll(".meta"));
  if (bareLines.join("|") !== "Answer in the message box below.") {
    throw new Error(`an options-less ask renders ${JSON.stringify(bareLines)}`);
  }
  if (choice.childNodes[0].textContent !== "Schedule — When should the deploy run?") {
    throw new Error(`header rule renders "${choice.childNodes[0].textContent}"`);
  }
  const buttons = choice.querySelectorAll("button");
  if (texts(buttons).join("|") !== "Now|Later") {
    throw new Error(`buttonable options render as ${texts(buttons).join("|")}`);
  }
  if (buttons[0].title !== "Right away" || buttons[1].title !== "") {
    throw new Error("button description tooltips wrong");
  }
  const multiLines = texts(multi.querySelectorAll(".meta"));
  const expectedMulti = [
    "Prod — Production servers",
    "Stage",
    "Select all that apply — answer in the message box below.",
  ];
  if (multiLines.join("|") !== expectedMulti.join("|")) {
    throw new Error(`multi-select fallback renders ${JSON.stringify(multiLines)}`);
  }
  const freeLines = texts(free.querySelectorAll(".meta"));
  if (freeLines.join("|") !== "Skip|Answer in the message box below.") {
    throw new Error(`free-text-with-options fallback renders ${JSON.stringify(freeLines)}`);
  }
  const attachLines = texts(attach.querySelectorAll(".meta"));
  if (attachLines.join("|") !== "Attach|Answer in the message box below.") {
    throw new Error(`allow_attachments fallback renders ${JSON.stringify(attachLines)}`);
  }
  const wideLines = texts(wide.querySelectorAll(".meta"));
  if (wideLines.length !== 12 || wideLines[11] !== "Answer in the message box below.") {
    throw new Error(`over-MAX_ANSWER_BUTTONS fallback renders ${wideLines.length} lines`);
  }
  await buttons[1].fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (wire.answered.length !== 1) throw new Error(`answer posted ${wire.answered.length} requests`);
  const sibling = wire.answered[0];
  if (sibling.body !== "Later · When should the deploy run?") {
    throw new Error(`sibling answer body is "${sibling.body}"`);
  }
  if (
    sibling.headers["x-ufo-answer-turn"] !== "t1"
    || sibling.headers["x-ufo-answer-question"] !== "1"
  ) {
    throw new Error(`answer headers are ${JSON.stringify(sibling.headers)}`);
  }
  if (byId.log.querySelectorAll(".qrow").length !== 5) {
    throw new Error("answered row did not retire");
  }
  await tabNamed("overview").fire("click");
  const audienceLine = byId.panel
    .querySelectorAll("*")
    .find((node) => node.className.includes("mono") && node.textContent === "every member");
  if (!audienceLine) {
    throw new Error("the main agent's overview does not state its audience as every member");
  }
  const form = byId.panel.querySelector("form");
  if (!form) throw new Error("overview built no form");
  if (form.className) throw new Error(`settings form carries class "${form.className}"`);
  const model = form.querySelector("select[data-key]");
  if (!model) throw new Error("overview built no model select");
  const target = model.childNodes.find((option) => option.value === "claude-opus-4-8");
  if (!target) throw new Error("model select carries no claude-opus-4-8 option");
  model.value = target.value;
  await form.fire("submit");
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (wire.posted.length !== 1) throw new Error(`save posted ${wire.posted.length} intents`);
  const canonical = (value) =>
    JSON.stringify(value, (key, node) =>
      node && typeof node === "object" && !Array.isArray(node)
        ? Object.fromEntries(Object.entries(node).sort())
        : node);
  const envelope = canonical(wire.posted[0]);
  const expected = canonical({
    verb: "apply",
    kind: "agent",
    name: "assistant",
    spec: { model: "claude-opus-4-8", internet_access_allowed: true },
  });
  if (envelope !== expected) {
    throw new Error(`save posted ${envelope}, expected ${expected}`);
  }
  const liveResult = byId.panel
    .querySelectorAll("*")
    .find((node) => node.className.includes("result"));
  if (!liveResult || !liveResult.isConnected || liveResult.textContent !== "Saved.") {
    throw new Error("save outcome not rendered on the live panel");
  }
  const updatedLine = byId.panel
    .querySelectorAll("*")
    .find((node) => node.textContent.includes("updated "));
  if (!updatedLine || !updatedLine.textContent.includes("2026-07-30 12:00")) {
    throw new Error("panel not re-read after save: " + updatedLine?.textContent);
  }
  const headModel = byId["agent-head"].childNodes.at(-1);
  if (!headModel || headModel.textContent !== "claude-opus-4-8") {
    throw new Error(`agent header reads "${headModel?.textContent}" after save`);
  }
  const rebuilt = byId.agents.querySelectorAll("button");
  const savedButton = rebuilt.find((button) =>
    button.childNodes[0]?.textContent.includes("assistant"));
  if (!savedButton || savedButton.childNodes[1]?.textContent !== "claude-opus-4-8") {
    throw new Error("sidebar not rebuilt with the saved model");
  }
  if (savedButton.attributes["aria-current"] !== "true") {
    throw new Error("saved agent lost its aria-current stamp");
  }
  const adminClicks = byId.admin.listeners.click ?? [];
  if (adminClicks.length !== 1) {
    throw new Error(`admin carries ${adminClicks.length} listeners after save`);
  }
  let releasePost;
  wire.holdPost = new Promise((resolve) => { releasePost = resolve; });
  const fencedForm = byId.panel.querySelector("form");
  const inFlight = fencedForm.fire("submit");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const sidebarNow = byId.agents.querySelectorAll("button");
  await sidebarNow[1].fire("click");
  releasePost();
  await inFlight;
  await new Promise((resolve) => setTimeout(resolve, 0));
  const fencedHead = byId["agent-head"].childNodes[0];
  if (!fencedHead || fencedHead.textContent !== "ops") {
    throw new Error(`header reads "${fencedHead?.textContent}" after a mid-save agent switch`);
  }
  wire.holdPost = null;
  const backButtons = byId.agents.querySelectorAll("button");
  await backButtons[0].fire("click");
  await tabNamed("overview").fire("click");
  for (const [arm, disarm, expected] of [
    [
      () => { wire.overviewStatus = 503; },
      () => { wire.overviewStatus = null; },
      "Saved. The panel could not be re-read: Error 503 — reload to retry.",
    ],
    [
      () => { wire.overviewThrows = true; },
      () => { wire.overviewThrows = false; },
      "Saved. The panel could not be re-read: Network error — try again.",
    ],
  ]) {
    const refreshForm = byId.panel.querySelector("form");
    if (!refreshForm) throw new Error("overview lost its form before the failed-refresh leg");
    arm();
    await refreshForm.fire("submit");
    await new Promise((resolve) => setTimeout(resolve, 0));
    disarm();
    const panelText = byId.panel
      .querySelectorAll("*")
      .map((node) => node.textContent)
      .filter((line) => line)
      .join(" | ");
    if (panelText !== expected) {
      throw new Error(`failed re-read panel reads "${panelText}", expected "${expected}"`);
    }
    await tabNamed("overview").fire("click");
  }
  await tabNamed("skills").fire("click");
  const skillForm = byId.panel.querySelectorAll("form").at(-1);
  if (!skillForm) throw new Error("skills tab built no save form");
  const skillName = skillForm.querySelector("input");
  const skillContent = skillForm.querySelector("textarea");
  if (!skillName || !skillContent) throw new Error("skill form lost its name or content field");
  const intentsBefore = wire.posted.length;
  skillName.value = "release-notes";
  skillContent.value = "---\nname: release-notes\ndescription: d\n---\nWrite tersely.";
  await skillForm.fire("submit");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const savedEnvelope = canonical(wire.posted[intentsBefore]);
  const expectedSave = canonical({
    verb: "apply",
    kind: "skill",
    name: "release-notes",
    spec: { files: { "SKILL.md": skillContent.value } },
  });
  if (savedEnvelope !== expectedSave) {
    throw new Error(`skill save posted ${savedEnvelope}, expected ${expectedSave}`);
  }
  const skillOutcome = byId.panel
    .querySelectorAll("*")
    .find((node) => node.className.includes("result"));
  if (!skillOutcome || !skillOutcome.isConnected || skillOutcome.textContent !== "Saved.") {
    throw new Error("skill save outcome not rendered on the refreshed panel");
  }
  const refreshedName = byId.panel.querySelectorAll("form").at(-1).querySelector("input");
  if (refreshedName.value !== "") {
    throw new Error("the skills panel did not re-read after the save");
  }
  const deleteButton = byId.panel
    .querySelectorAll("button")
    .find((node) => node.textContent === "Delete");
  if (!deleteButton) throw new Error("the member skill row carries no Delete control");
  await deleteButton.fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const deleteEnvelope = canonical(wire.posted[intentsBefore + 1]);
  const expectedDelete = canonical({ verb: "delete", kind: "skill", name: "release-notes" });
  if (deleteEnvelope !== expectedDelete) {
    throw new Error(`skill delete posted ${deleteEnvelope}, expected ${expectedDelete}`);
  }
  await tabNamed("usage").fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const usageWall = byId.panel
    .querySelectorAll("*")
    .find((node) => node.textContent === "Usage for this agent is not shared with you.");
  if (!usageWall) throw new Error("the usage 404 renders no member line");
  await tabNamed("connections").fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const connectionRows = byId.panel.querySelectorAll("tr").slice(1);
  if (connectionRows.length !== 2) {
    throw new Error(`connections rendered ${connectionRows.length} rows`);
  }
  const ownedActions = connectionRows[0].querySelectorAll("button");
  if (texts(ownedActions).join("|") !== "Share with agent|Revoke") {
    throw new Error(`owned row actions are ${texts(ownedActions).join("|")}`);
  }
  if (connectionRows[1].querySelectorAll("button").length !== 0) {
    throw new Error("a foreign edge rendered owner actions");
  }
  await ownedActions[0].fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const flipEnvelope = wire.posted.at(-1);
  if (JSON.stringify(flipEnvelope) !== JSON.stringify({
    verb: "apply",
    kind: "connector_grant",
    name: "github-gh-acct-abcd1234",
    spec: { provider: "github", account_id: "gh-acct", shared: true },
  })) {
    throw new Error(`flip posted ${JSON.stringify(flipEnvelope)}`);
  }
  const flipNotice = byId.panel
    .querySelectorAll("*")
    .find((node) => node.className.includes("result") && node.textContent === "Saved.");
  if (!flipNotice) throw new Error("the flip outcome did not render on the re-read panel");
  const rowsAfterFlip = byId.panel.querySelectorAll("tr").slice(1);
  await rowsAfterFlip[0].querySelectorAll("button")[1].fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const revokeEnvelope = wire.posted.at(-1);
  if (JSON.stringify(revokeEnvelope) !== JSON.stringify({
    verb: "delete",
    kind: "connector_grant",
    name: "github-gh-acct-abcd1234",
  })) {
    throw new Error(`revoke posted ${JSON.stringify(revokeEnvelope)}`);
  }
  wire.connectUrl = "https://oauth.example.test/authorize?state=s1";
  const connectForm = byId.panel.querySelector("form.search");
  if (!connectForm) throw new Error("connections built no connect form");
  const providerField = connectForm.querySelectorAll("input")[0];
  const shareBox = connectForm.querySelectorAll("input")[1];
  providerField.value = "notion";
  shareBox.checked = true;
  await connectForm.fire("submit");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const connectEnvelope = wire.posted.at(-1);
  if (JSON.stringify(connectEnvelope) !== JSON.stringify({
    verb: "connect",
    kind: "connection",
    name: "notion",
    spec: { shared: true },
  })) {
    throw new Error(`connect posted ${JSON.stringify(connectEnvelope)}`);
  }
  const consentLink = byId.panel
    .querySelectorAll("a")
    .find((node) => node.textContent === "Open the provider consent page");
  if (!consentLink || consentLink.href !== "https://oauth.example.test/authorize?state=s1") {
    throw new Error("the private consent link did not render from the stream");
  }
  if (consentLink.target !== "_blank") throw new Error("consent link must open its own tab");
  wire.connectUrl = null;
  const agentTabs = texts(byId.tabs.childNodes);
  const expectedTabs = ["chat", "overview", "tasks", "connections", "skills", "usage"];
  if (agentTabs.join("|") !== expectedTabs.join("|")) {
    throw new Error(`the agent tab strip reads ${JSON.stringify(agentTabs)}`);
  }
  const workspaceButtons = byId.workspace.querySelectorAll("button");
  const workspaceLabels = texts(workspaceButtons);
  const expectedWorkspace = [
    "Sources", "Credentials", "Memory", "Artifacts", "Sites", "Usage",
  ];
  if (workspaceLabels.join("|") !== expectedWorkspace.join("|")) {
    throw new Error(`the workspace sidebar reads ${JSON.stringify(workspaceLabels)}`);
  }
  const workspaceNamed = (label) =>
    workspaceButtons.find((button) => button.textContent === label)
    ?? (() => { throw new Error(`no ${label} workspace item`); })();
  const viewText = () =>
    body
      .querySelector(".admin-view")
      .querySelectorAll("*")
      .map((node) => node.textContent)
      .filter((line) => line)
      .join(" | ");
  const viewCells = () =>
    texts(
      body
        .querySelector(".admin-view")
        .querySelectorAll("*")
        .filter((node) => node.tagName === "th" || node.tagName === "td")
    );

  await workspaceNamed("Sources").fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const sourceCells = viewCells();
  const expectedSources = [
    "source", "streams", "owner", "access", "errors", "next sync", "",
    "gmail", "messages, threads", "admin@example.com", "private", "1", "2026-07-30 12:00", "",
    "folder", "—", "—", "shared", "0", "2026-07-30 11:00", "",
  ];
  if (sourceCells.join("|") !== expectedSources.join("|")) {
    throw new Error(`the sources view reads ${JSON.stringify(sourceCells)}`);
  }
  const sourceRows = body.querySelector(".admin-view").querySelectorAll("tr").slice(1);
  const acts = sourceRows[0].querySelectorAll("button");
  if (texts(acts).join("|") !== "Resync|Share|Remove") {
    throw new Error(`binding actions are ${texts(acts).join("|")}`);
  }
  if (sourceRows[1].querySelectorAll("button").length !== 0) {
    throw new Error("a source the kind does not manage offered acts");
  }
  wire.workspace.sources.sources = wire.workspace.sources.sources.map(
    (entry) => (entry.name === null ? entry : { ...entry, shared: true })
  );
  await workspaceNamed("Sources").fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const sharedActs = body.querySelector(".admin-view").querySelectorAll("tr")[1]
    .querySelectorAll("button");
  if (texts(sharedActs).join("|") !== "Resync|Remove") {
    throw new Error(`an already-shared binding offers ${texts(sharedActs).join("|")}`);
  }
  wire.workspace.sources.sources = wire.workspace.sources.sources.map(
    (entry) => (entry.name === null ? entry : { ...entry, shared: false })
  );
  await workspaceNamed("Sources").fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const liveActs = body.querySelector(".admin-view").querySelectorAll("tr")[1]
    .querySelectorAll("button");
  const sourceActsBefore = wire.posted.length;
  const bindingSpec = {
    provider: "gmail",
    streams: ["messages", "threads"],
    account_id: "acct-1",
    base_url: "",
    shared: false,
  };
  await liveActs[0].fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (JSON.stringify(wire.posted[sourceActsBefore]) !== JSON.stringify({
    verb: "apply", kind: "source", name: "gmail-1a2b3c4d",
    spec: { ...bindingSpec, resync: true },
  })) {
    throw new Error(`resync posted ${JSON.stringify(wire.posted[sourceActsBefore])}`);
  }
  if (!wire.intentUrls[sourceActsBefore].includes(AGENT_A)) {
    throw new Error("a workspace source act did not ride the main agent's lane");
  }
  const shareButton = body.querySelector(".admin-view").querySelectorAll("button")
    .find((node) => node.textContent === "Share");
  await shareButton.fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (JSON.stringify(wire.posted[sourceActsBefore + 1]) !== JSON.stringify({
    verb: "apply", kind: "source", name: "gmail-1a2b3c4d",
    spec: { ...bindingSpec, shared: true },
  })) {
    throw new Error(`share posted ${JSON.stringify(wire.posted[sourceActsBefore + 1])}`);
  }
  const removeButton = body.querySelector(".admin-view").querySelectorAll("button")
    .find((node) => node.textContent === "Remove");
  await removeButton.fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (JSON.stringify(wire.posted[sourceActsBefore + 2]) !== JSON.stringify({
    verb: "delete", kind: "source", name: "gmail-1a2b3c4d",
  })) {
    throw new Error(`remove posted ${JSON.stringify(wire.posted[sourceActsBefore + 2])}`);
  }
  const sourcesOutcome = body.querySelector(".admin-view").querySelectorAll("*")
    .find((node) => node.className.includes("result"));
  if (!sourcesOutcome || sourcesOutcome.textContent !== "Saved.") {
    throw new Error(`the sources outcome reads "${sourcesOutcome?.textContent}"`);
  }
  if (sandbox.location.hash !== "#/workspace/sources") {
    throw new Error(`a workspace view left the hash at "${sandbox.location.hash}"`);
  }
  if (workspaceNamed("Sources").attributes["aria-current"] !== "true") {
    throw new Error("the open workspace item carries no aria-current stamp");
  }
  if (byId.agents.querySelectorAll("button").some(
    (button) => button.attributes["aria-current"] === "true")) {
    throw new Error("an agent stayed marked current under a workspace view");
  }
  wire.workspace.sources.sources = [];
  await workspaceNamed("Sources").fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const emptySources = 'No sources are registered. Register one in chat — the agent connects '
    + 'the account or credential it needs as part of the request.';
  if (viewText() !== emptySources) {
    throw new Error(`the empty sources view reads "${viewText()}"`);
  }

  await workspaceNamed("Credentials").fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const slotCells = viewCells();
  const expectedSlots = [
    "slot", "description", "extension", "state", "",
    "api_key", "Acme API key", "acme", "empty", "",
    "signing_key", "Signing key", "acme", "filled", "",
  ];
  if (slotCells.join("|") !== expectedSlots.join("|")) {
    throw new Error(`the credentials view reads ${JSON.stringify(slotCells)}`);
  }
  const slotRows = body.querySelectorAll("tr");
  const emptyActions = slotRows[1].childNodes.at(-1).querySelectorAll("button");
  if (emptyActions.length !== 1 || emptyActions[0].textContent !== "Set") {
    throw new Error("an empty slot offers something other than Set alone");
  }
  await emptyActions[0].fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (wire.intentUrls.at(-1) !== `/surface/web/agents/${AGENT_A}/intents`) {
    throw new Error(`the credential intent posted to ${wire.intentUrls.at(-1)}`);
  }
  if (JSON.stringify(wire.posted.at(-1)) !== JSON.stringify(
    { verb: "request", kind: "credential", name: "api-key" })) {
    throw new Error(`the credential intent submitted ${JSON.stringify(wire.posted.at(-1))}`);
  }
  const secretField = body.querySelector("input[type]");
  const promptForm = body.querySelectorAll("form").at(-1);
  if (!promptForm || !secretField || secretField.type !== "password") {
    throw new Error("the minted prompt rendered no password field");
  }
  secretField.value = "s3cr3t";
  await promptForm.fire("submit");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const fulfilled = wire.credentialPosts.at(-1);
  if (!fulfilled || !fulfilled.includes("sealed-from-the-turn")) {
    throw new Error(`the fulfillment posted ${fulfilled}`);
  }
  if (!fulfilled.includes("s3cr3t")) throw new Error("the secret never reached the sealed post");
  const filledActions = body
    .querySelectorAll("tr")[2].childNodes.at(-1).querySelectorAll("button");
  if (filledActions.map((button) => button.textContent).join("|") !== "Replace|Clear") {
    throw new Error("a filled slot offers something other than Replace and Clear");
  }
  await filledActions[1].fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (JSON.stringify(wire.posted.at(-1)) !== JSON.stringify(
    { verb: "delete", kind: "credential", name: "signing-key" })) {
    throw new Error(`Clear submitted ${JSON.stringify(wire.posted.at(-1))}`);
  }
  wire.workspace.credentials.slots = [];
  await workspaceNamed("Credentials").fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (viewText() !== "No credential slots are declared.") {
    throw new Error(`the empty credentials view reads "${viewText()}"`);
  }

  await workspaceNamed("Memory").fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const searchForm = body.querySelector("form.search");
  if (!searchForm) throw new Error("the memory view built no form.search search bar");
  for (const rule of ["\n  form.search {", "\n  form.search input {"]) {
    if (!html.includes(rule)) throw new Error(`the page no longer styles ${rule.trim()}`);
  }
  if (html.includes("#panel form {") || html.includes("#panel form input {")) {
    throw new Error("#panel form styling rescoped past the search bar onto the settings form");
  }
  if (wire.memoryQueries.at(-1) !== null) {
    throw new Error(`the memory listing sent q=${wire.memoryQueries.at(-1)}`);
  }
  const listingCells = viewCells();
  const expectedListing = [
    "memory", "kind", "ref", "date", "",
    "the launch codename is bluebird", "fact", "memory/1", "2026-07-29 10:00", "",
    "the codename page", "source", "page/9", "2026-07-29 09:00", "—",
  ];
  if (listingCells.join("|") !== expectedListing.join("|")) {
    throw new Error(`the memory listing reads ${JSON.stringify(listingCells)}`);
  }
  const listedCorrect = body
    .querySelector(".admin-view")
    .querySelectorAll("button")
    .filter((button) => button.textContent === "Correct");
  if (listedCorrect.length !== 1) {
    throw new Error(
      `${listedCorrect.length} listed rows offer correction — only the memory item may`
    );
  }
  await listedCorrect[0].fire("click");
  const correctionForm = body.querySelector("form.correct");
  if (!correctionForm) throw new Error("the correction control built no form.correct");
  for (const rule of ["\n  form.correct {", "\n  form.correct input {"]) {
    if (!html.includes(rule)) throw new Error(`the page no longer styles ${rule.trim()}`);
  }
  const correctionBody = correctionForm.querySelector("input");
  if (correctionBody.value !== "the launch codename is bluebird") {
    throw new Error(`the correction form prefills "${correctionBody.value}"`);
  }
  const intentsBeforeCorrection = wire.posted.length;
  correctionBody.value = "  the launch codename is redwood  ";
  wire.memory.recent = [
    {
      kind: "fact",
      text: "the launch codename is redwood",
      ref: "memory/2",
      created_at: "2026-07-30T11:00:00+00:00",
    },
  ];
  await correctionForm.fire("submit");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const correction = canonical(wire.posted[intentsBeforeCorrection]);
  const expectedCorrection = canonical({
    verb: "record",
    kind: "memory",
    corrects: "1",
    body: "the launch codename is redwood",
  });
  if (correction !== expectedCorrection) {
    throw new Error(`the correction posted ${correction}, expected ${expectedCorrection}`);
  }
  if (!wire.intentUrls.at(-1).includes(`/agents/${AGENT_A}/intents`)) {
    throw new Error(`the correction posted to ${wire.intentUrls.at(-1)}, not the main agent`);
  }
  if (!viewCells().includes("the launch codename is redwood")) {
    throw new Error("an applied correction did not re-read the memory view");
  }
  wire.intentRefuses = true;
  const refusedRow = body
    .querySelector(".admin-view")
    .querySelectorAll("button")
    .find((button) => button.textContent === "Correct");
  await refusedRow.fire("click");
  const refusedForm = body.querySelector("form.correct");
  refusedForm.querySelector("input").value = "refused text";
  await refusedForm.fire("submit");
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (!viewText().includes("Not applied.")) {
    throw new Error(`a refused correction reads "${viewText()}"`);
  }
  wire.intentRefuses = false;
  await workspaceNamed("Memory").fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const searchField = searchForm.querySelector("input");
  if (searchField.placeholder !== "Search memory…") {
    throw new Error(`the memory search field reads "${searchField.placeholder}"`);
  }
  if (texts(searchForm.querySelectorAll("button")).join("|") !== "Search") {
    throw new Error("the memory search bar carries no Search button");
  }
  searchField.value = "  runway  ";
  await searchForm.fire("submit");
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (wire.memoryQueries.at(-1) !== "runway") {
    throw new Error(`the memory search sent q=${JSON.stringify(wire.memoryQueries.at(-1))}`);
  }
  const searchedCells = viewCells();
  if (!searchedCells.includes("the runway is painted") || !searchedCells.includes("—")) {
    throw new Error(`the memory search reads ${JSON.stringify(searchedCells)}`);
  }
  if (body.querySelector("form.search").querySelector("input").value !== "runway") {
    throw new Error("the memory search bar dropped the submitted query");
  }
  wire.memory.searched = [];
  await body.querySelector("form.search").fire("submit");
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (!viewText().endsWith("No matches.")) {
    throw new Error(`an empty memory search reads "${viewText()}"`);
  }
  wire.memory.recent = [];
  await workspaceNamed("Memory").fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (!viewText().endsWith("No memories yet.")) {
    throw new Error(`an empty memory listing reads "${viewText()}"`);
  }
  wire.memory.available = false;
  await workspaceNamed("Memory").fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (!viewText().endsWith("This deploy has no memory extension.")) {
    throw new Error(`a memoryless deploy reads "${viewText()}"`);
  }

  await workspaceNamed("Artifacts").fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const artifactView = body.querySelector(".admin-view");
  const artifactHeaders = texts(artifactView.querySelectorAll("th"));
  if (artifactHeaders.join("|") !== "file|subject|type|size|date") {
    throw new Error(`the artifacts view heads ${JSON.stringify(artifactHeaders)}`);
  }
  const download = artifactView.querySelector("a");
  const shared = wire.workspace.artifacts.artifacts[0];
  if (!download || download.href !== shared.url || download.textContent !== shared.filename) {
    throw new Error(`the artifact filename links to "${download?.href}"`);
  }
  const [linked, ...artifactCells] = texts(artifactView.querySelectorAll("td"));
  if (linked !== "") throw new Error(`the linked filename cell also reads "${linked}"`);
  const expectedArtifacts = [
    "the quarterly numbers", "application/pdf", "2 kB", "2026-07-29 11:00",
  ];
  if (artifactCells.join("|") !== expectedArtifacts.join("|")) {
    throw new Error(`the artifacts view reads ${JSON.stringify(artifactCells)}`);
  }
  shared.url = null;
  await workspaceNamed("Artifacts").fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const undelivered = body.querySelector(".admin-view");
  if (undelivered.querySelector("a")) {
    throw new Error("an artifact with no delivery link still rendered a link");
  }
  if (texts(undelivered.querySelectorAll("td"))[0] !== "report.pdf") {
    throw new Error("an artifact with no delivery link lost its filename");
  }
  wire.workspace.artifacts.artifacts = [];
  await workspaceNamed("Artifacts").fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (viewText() !== "No shared files yet.") {
    throw new Error(`the empty artifacts view reads "${viewText()}"`);
  }

  await workspaceNamed("Sites").fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const siteCells = viewCells();
  const expectedSites = ["site", "summary", "landing-ab12cd34", "port 3000, shared"];
  if (siteCells.join("|") !== expectedSites.join("|")) {
    throw new Error(`the sites view reads ${JSON.stringify(siteCells)}`);
  }
  wire.workspace.sites.sites = [];
  await workspaceNamed("Sites").fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (viewText() !== "No sites are hosted.") {
    throw new Error(`the empty sites view reads "${viewText()}"`);
  }
  wire.workspace.sites.available = false;
  await workspaceNamed("Sites").fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (viewText() !== "No sites extension is installed.") {
    throw new Error(`a sitesless deploy reads "${viewText()}"`);
  }

  await workspaceNamed("Usage").fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const adminUsage = viewText();
  const expectedAdminUsage = [
    "Your spend · last 24h · $1.500000",
    "dimension | units | cost | tokens | 4,200 | $1.500000",
    "Your caps",
    "window | limit | on breach | 1h | $5.00 | park",
    "Workspace · $9.000000",
    "dimension | units | cost | egress | 7 | $9.000000",
    "By member",
    "subject | cost | admin@example.com | $9.000000",
    "By agent",
    "subject | cost | assistant | $9.000000",
  ].join(" | ");
  if (adminUsage !== expectedAdminUsage) {
    throw new Error(`the admin usage view reads "${adminUsage}"`);
  }
  wire.workspace.usage.workspace = null;
  wire.workspace.usage.by_dimension = [];
  wire.workspace.usage.caps = [];
  await workspaceNamed("Usage").fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const memberUsage = viewText();
  const expectedMemberUsage = [
    "Your spend · last 24h · $1.500000",
    "No spend of yours in window.",
    "Your caps",
    "No caps are set on you.",
  ].join(" | ");
  if (memberUsage !== expectedMemberUsage) {
    throw new Error(`a non-admin usage view reads "${memberUsage}"`);
  }
  if (memberUsage.includes("@") || memberUsage.includes("assistant")) {
    throw new Error("a non-admin usage view named another subject");
  }

  wire.workspaceStatus = 500;
  await workspaceNamed("Sites").fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (viewText() !== "Error 500 — reload to retry.") {
    throw new Error(`a failed workspace read reads "${viewText()}"`);
  }
  wire.workspaceStatus = null;
  wire.workspaceThrows = true;
  await workspaceNamed("Sites").fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (viewText() !== "Network error — try again.") {
    throw new Error(`a dropped workspace read reads "${viewText()}"`);
  }
  const restored = byId.agents.querySelectorAll("button");
  await restored[0].fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (!byId["agent-head"].isConnected) throw new Error("chat pane not restored after a workspace view");
  if (workspaceButtons.some((button) => button.attributes["aria-current"] === "true")) {
    throw new Error("a workspace item stayed marked current under an agent");
  }
  await tabNamed("overview").fire("click");
  await byId.admin.fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const adminCells = body.querySelectorAll("td").map((cell) => cell.textContent);
  if (!adminCells.includes("every member")) {
    throw new Error("the admin view does not state the main agent's audience as every member");
  }
  for (const cell of ["assistant", "24h", "$5.00", "park"]) {
    if (!adminCells.includes(cell)) {
      throw new Error(`the billing caps table misses "${cell}"`);
    }
  }
  const adminLines = body.querySelectorAll("div").map((node) => node.textContent);
  if (!adminLines.includes(
    "The plan, invoices, and payment methods are managed with the agent in chat."
  )) {
    throw new Error("the billing section misses the plan-and-invoices line");
  }
  if (!adminLines.includes("Sandbox public internet: blocked")) {
    throw new Error("the deploy section misses the sandbox ceiling line");
  }
  if (!adminCells.includes("web") || !adminCells.includes("0.1.0")) {
    throw new Error("the deploy extensions table misses the manifest row");
  }
  const adminView = () => body.querySelector(".admin-view");
  const buttonNamed = (label) => {
    const found = adminView().querySelectorAll("button").find((b) => b.textContent === label);
    if (!found) throw new Error(`the admin view carries no ${label} control`);
    return found;
  };
  const createForm = adminView().querySelectorAll("form")
    .find((form) => form.className === "admin-create");
  if (!createForm) throw new Error("the admin view built no create-agent form");
  const createInputs = createForm.querySelectorAll("input");
  const modelSelect = createForm.querySelector("select");
  if (texts(modelSelect.childNodes).join("|") !== "auto|claude-opus-4-8") {
    throw new Error("the create form's model choice is not the deploy's model list");
  }
  createInputs[0].value = "research";
  createForm.querySelector("textarea").value = "be curious";
  const beforeCreate = wire.posted.length;
  await createForm.fire("submit");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const createEnvelope = wire.posted[beforeCreate];
  if (JSON.stringify(createEnvelope) !== JSON.stringify({
    verb: "apply",
    kind: "agent",
    name: "research",
    spec: { model: "auto", internet_access_allowed: true, prompt: "be curious" },
  })) {
    throw new Error(`create posted ${JSON.stringify(createEnvelope)}`);
  }
  if (!wire.intentUrls[beforeCreate].includes(AGENT_A)) {
    throw new Error("the create intent did not ride the main agent's lane");
  }
  const createResult = adminView().querySelectorAll("*")
    .find((node) => node.className.includes("result"));
  if (!createResult || createResult.textContent !== "Created research. Saved.") {
    throw new Error(`create result reads "${createResult?.textContent}"`);
  }
  const memberRow = adminView().querySelectorAll("tr")
    .find((rowNode) => rowNode.childNodes[0]?.textContent === "m@example.com");
  if (!memberRow) throw new Error("the members table lists no m@example.com row");
  const unseat = memberRow.querySelectorAll("button").find((b) => b.textContent === "Unseat");
  if (!unseat) throw new Error("the m@example.com row carries no Unseat control");
  await unseat.fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const seatEnvelope = wire.posted[wire.posted.length - 1];
  if (JSON.stringify(seatEnvelope) !== JSON.stringify({
    verb: "apply",
    kind: "member",
    name: "44444444-4444-4444-8444-444444444444",
    spec: { admin: false, seated: false },
  })) {
    throw new Error(`seat toggle posted ${JSON.stringify(seatEnvelope)}`);
  }
  const audienceEmail = adminView().querySelectorAll("td")
    .flatMap((cellNode) => cellNode.querySelectorAll("input"))
    .find((input) => input.placeholder === "email@work.com");
  if (!audienceEmail) throw new Error("the ops row carries no audience email field");
  audienceEmail.value = "m@example.com";
  await buttonNamed("Grant").fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const grantEnvelope = wire.posted[wire.posted.length - 1];
  if (JSON.stringify(grantEnvelope) !== JSON.stringify({
    verb: "grant_web_access",
    email: "m@example.com",
  })) {
    throw new Error(`grant posted ${JSON.stringify(grantEnvelope)}`);
  }
  if (!wire.intentUrls[wire.intentUrls.length - 1].includes(AGENT_B)) {
    throw new Error("the grant intent did not ride the target agent's lane");
  }
  const freshButtons = byId.agents.querySelectorAll("button");
  await freshButtons[1].fire("click");
  await tabNamed("overview").fire("click");
  if (!byId["agent-head"].isConnected) throw new Error("chat pane not restored after admin");
  if (!byId.panel.isConnected) throw new Error("overview panel not restored after admin");
  if (!byId.tabs.isConnected) throw new Error("tabs not restored after admin");
  await tabNamed("chat").fire("click");
  const credentialField = byId.log.querySelector("input");
  if (!credentialField || credentialField.placeholder !== "api_key") {
    throw new Error("credentials handoff rendered no api_key field");
  }
  if (credentialField.type !== "password") {
    throw new Error(`the secret field is type "${credentialField.type}"`);
  }
  const credentialForm = credentialField.parentNode;
  const credentialLabel = credentialForm.parentNode.childNodes[0];
  const storeButton = credentialForm.querySelectorAll("button")[0];
  const postsBefore = wire.credentialPosts.length;
  credentialField.value = "   ";
  await credentialForm.fire("submit");
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (wire.credentialPosts.length !== postsBefore) {
    throw new Error("a whitespace value reached the wire");
  }
  wire.credentialThrows = true;
  wire.credentialRefuses = true;
  credentialField.value = "sk-live";
  await credentialForm.fire("submit");
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (credentialLabel.textContent !== "Acme API key — network error, try again.") {
    throw new Error(`network-error label reads "${credentialLabel.textContent}"`);
  }
  if (storeButton.disabled) throw new Error("Store stayed wedged after a network error");
  credentialField.value = "x".repeat(9000);
  await credentialForm.fire("submit");
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (credentialLabel.textContent !== "Acme API key — value too large.") {
    throw new Error(`refusal label reads "${credentialLabel.textContent}"`);
  }
  if (storeButton.disabled) throw new Error("Store stayed wedged after a refusal");
  credentialField.value = "sk-live";
  await credentialForm.fire("submit");
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (credentialLabel.textContent !== "Stored api_key.") {
    throw new Error(`stored label reads "${credentialLabel.textContent}"`);
  }
  if (credentialForm.isConnected) throw new Error("stored prompt kept its form");
  const chatPosts = wire.credentialPosts.slice(postsBefore);
  if (chatPosts.length !== 2 || !chatPosts[1].includes("sealed-blob")) {
    throw new Error(`credential posts were ${JSON.stringify(chatPosts)}`);
  }
  if (byId.log.querySelectorAll(".files").length !== 1) {
    throw new Error("the transcript's files handoff rendered no download box");
  }
  const loneRow = byId.log.querySelector(".qrow");
  if (!loneRow) throw new Error("lone question rendered no row");
  const loneButtons = loneRow.querySelectorAll("button");
  if (texts(loneButtons).join("|") !== "Ship|Hold") {
    throw new Error(`lone question renders ${texts(loneButtons).join("|")}`);
  }
  let releaseAnswer;
  wire.holdAnswer = new Promise((resolve) => { releaseAnswer = resolve; });
  const answerInFlight = loneButtons[0].fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  await byId.agents.querySelectorAll("button")[0].fire("click");
  releaseAnswer();
  wire.holdAnswer = null;
  await answerInFlight;
  await new Promise((resolve) => setTimeout(resolve, 0));
  const lone = wire.answered[1];
  if (lone.body !== "Ship") throw new Error(`lone answer body is "${lone.body}"`);
  if (lone.headers["x-ufo-answer-turn"] !== "t9") {
    throw new Error(`lone answer headers are ${JSON.stringify(lone.headers)}`);
  }
  const backRows = byId.log.querySelectorAll(".qrow");
  if (backRows.length !== 5) {
    throw new Error(`assistant re-render built ${backRows.length} rows — answered guard lost`);
  }
  if (backRows.some((row) => row.childNodes[0].textContent.includes("Schedule"))) {
    throw new Error("the answered question re-rendered with live rows");
  }
  await byId.agents.querySelectorAll("button")[1].fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (byId.log.querySelectorAll(".qrow").length !== 0) {
    throw new Error("a fully answered handoff re-rendered question rows");
  }
  if (byId.log.querySelectorAll(".panel").length !== 0) {
    throw new Error("a fully answered handoff left a stray panel");
  }
  if (byId.log.querySelectorAll(".files").length !== 0) {
    throw new Error("an older turn's files re-rendered after the answer turn superseded them");
  }
  const landedBubble = byId.log
    .querySelectorAll(".bubble")
    .find((node) => node.textContent === "server:Ship");
  if (!landedBubble) {
    throw new Error("the admitted answer body did not land as the member's message");
  }
  if (composer.querySelector("button.send").disabled) {
    throw new Error("composer stayed disabled after the answered turn");
  }
  await byId.agents.querySelectorAll("button")[0].fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  byId.msg.value = "all done";
  await composer.fire("submit");
  await new Promise((resolve) => setTimeout(resolve, 0));
  await byId.agents.querySelectorAll("button")[1].fire("click");
  await byId.agents.querySelectorAll("button")[0].fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (byId.log.querySelectorAll(".qrow").length !== 0) {
    throw new Error("a composer turn without a question left the stale ask re-rendering");
  }
  wire.signedOut = true;
  byId["token-card"].style.display = "none";
  run(...Object.values(sandbox));
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (byId["token-card"].style.display !== "block") {
    throw new Error("signed-out boot did not show the token card");
  }
  console.log("portal smoke ok");
} catch (error) {
  console.error(error);
  exit(1);
}
