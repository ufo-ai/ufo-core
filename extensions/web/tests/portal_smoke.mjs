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
nav.append(element("div"), element("ul", "agents"));
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
        name: "assistant",
        main: true,
        model: "auto",
        internet_access_allowed: true,
        installations: [],
        web_audience: [],
      },
    ],
    members: [{ email: "admin@example.com", admin: true, seated: true }],
    seats: { limit: null, included: null },
  },
  posted: [],
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
      if (wire.credentialPosts.length === 1) {
        return { ok: false, status: 413, text: async () => "value too large" };
      }
      return { ok: true, status: 200, json: async () => ({ stored: "api_key" }) };
    }
    if (options?.method === "POST" && url.endsWith("/intents")) {
      wire.posted.push(JSON.parse(options.body));
      if (wire.holdPost) await wire.holdPost;
      wire.overview.agent.updated_at = "2026-07-30T12:00:00+00:00";
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
    if (url.endsWith("/usage")) {
      return { ok: false, status: 404, json: async () => ({}) };
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
  await tabNamed("usage").fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const usageWall = byId.panel
    .querySelectorAll("*")
    .find((node) => node.textContent === "Usage for this agent is not shared with you.");
  if (!usageWall) throw new Error("the usage 404 renders no member line");
  await tabNamed("memory").fire("click");
  const searchForm = byId.panel.querySelector("form");
  if (!searchForm || searchForm.className !== "search") {
    throw new Error("memory tab built no form.search search bar");
  }
  for (const rule of ["#panel form.search {", "#panel form.search input {"]) {
    if (!html.includes(rule)) throw new Error(`the page no longer styles ${rule}`);
  }
  if (html.includes("#panel form {") || html.includes("#panel form input {")) {
    throw new Error("#panel form styling rescoped past the search bar onto the settings form");
  }
  await byId.admin.fire("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
  const adminCells = body.querySelectorAll("td").map((cell) => cell.textContent);
  if (!adminCells.includes("every member")) {
    throw new Error("the admin view does not state the main agent's audience as every member");
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
  credentialField.value = "   ";
  await credentialForm.fire("submit");
  await new Promise((resolve) => setTimeout(resolve, 0));
  if (wire.credentialPosts.length !== 0) {
    throw new Error("a whitespace value reached the wire");
  }
  wire.credentialThrows = true;
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
  if (wire.credentialPosts.length !== 2 || !wire.credentialPosts[1].includes("sealed-blob")) {
    throw new Error(`credential posts were ${JSON.stringify(wire.credentialPosts)}`);
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
