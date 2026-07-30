// Reference-level smoke over the portal page's script: a stub DOM whose ids resolve only when
// the page's own markup carries them, canned wire responses, and a walk through every view
// transition — boot (signed in and the 401 token-card branch), select, overview, the settings
// save (the exact posted envelope, outcome rendered on the refreshed panel, header and sidebar
// refreshed with the aria-current stamp, no listener accumulation), the mid-save agent switch,
// the failed post-save re-read (both legs, the outcome standing), the memory tab's search bar
// (its form.search class and both style rules scoped to it), admin, select-after-admin — so a
// deleted declaration, a dangling element reference, or a dropped markup id throws here instead
// of rendering a blank portal. Run: node portal_smoke.mjs <path-to-portal.html>.

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
    child.parentNode?.childNodes.splice(child.parentNode.childNodes.indexOf(child), 1);
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }
  append(...nodes) {
    nodes.forEach((node) => this.appendChild(node));
  }
  replaceChildren(...nodes) {
    this.childNodes.forEach((child) => {
      child.parentNode = null;
    });
    this.childNodes = [];
    nodes.forEach((node) => this.appendChild(node));
  }
  addEventListener(name, handler) {
    (this.listeners[name] ??= []).push(handler);
  }
  async fire(name, event = {}) {
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
    const [tag, attr] = selector.split("[");
    if (tag && tag !== "*" && this.tagName !== tag) return false;
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
nav.append(element("div"), element("ul", "agents"));
const footer = element("footer");
footer.append(element("span", "member-email"), element("a", "spend"), element("button", "admin"));
nav.appendChild(footer);
const agentHead = element("div", "agent-head");
const composer = element("form", "composer");
composer.append(element("input", "msg"), element("button"));
main.append(agentHead, element("div", "tabs"), element("div", "log"), element("div", "panel"), composer);
const tokenForm = element("form", "token-form");
tokenCard.appendChild(tokenForm);
for (const id of pageIds) {
  if (!(id in byId)) throw new Error(`page markup carries id "${id}" the stub does not build`);
}

const AGENT_A = "11111111-1111-4111-8111-111111111111";
const AGENT_B = "22222222-2222-4222-8222-222222222222";
const wire = {
  "/api/agents": {
    member: { email: "admin@example.com", admin: true },
    agents: [
      { id: AGENT_A, name: "assistant", main: true, model: "auto" },
      { id: AGENT_B, name: "ops", main: false, model: "claude-sonnet-5" },
    ],
  },
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
  transcript: { messages: [{ role: "user", text: "hi" }, { role: "assistant", text: "hello" }] },
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
    getElementById: (id) => (pageIds.has(id) ? (byId[id] ?? null) : null),
    querySelector: (selector) => (selector === "nav" ? nav : selector === "main" ? main : body.querySelector(selector)),
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
    if (options?.method === "POST") {
      if (!url.endsWith("/intents")) throw new Error(`unstubbed POST: ${url}`);
      wire.posted.push(JSON.parse(options.body));
      if (wire.holdPost) await wire.holdPost;
      wire.overview.agent.updated_at = "2026-07-30T12:00:00+00:00";
      return {
        ok: true,
        status: 200,
        json: async () => ({ applied: true, message: "Saved.", turn_id: "t" }),
      };
    }
    if (url.endsWith("/api/agents") && wire.signedOut) {
      return { ok: false, status: 401, json: async () => ({}) };
    }
    if (url.endsWith("/overview") && wire.overviewThrows) throw new TypeError("network down");
    if (url.endsWith("/overview") && wire.overviewStatus) {
      return { ok: false, status: wire.overviewStatus, json: async () => ({}) };
    }
    const payload = url.endsWith("/transcript")
      ? wire.transcript
      : url.endsWith("/overview")
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
    addEventListener() {}
    close() {}
  },
  console,
};

const script = html.split("<script>")[1].split("</script>")[0];
const run = new Function(...Object.keys(sandbox), `"use strict";\n${script}`);

try {
  run(...Object.values(sandbox));
  await new Promise((resolve) => setTimeout(resolve, 0));
  const agentButtons = byId.agents.querySelectorAll("button");
  if (agentButtons.length !== 2) throw new Error(`sidebar built ${agentButtons.length} agents`);
  const tabNamed = (name) =>
    byId.tabs.childNodes.find((node) => node.textContent === name)
    ?? (() => { throw new Error(`no ${name} tab`); })();
  await tabNamed("overview").fire("click");
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
  const freshButtons = byId.agents.querySelectorAll("button");
  await freshButtons[1].fire("click");
  await tabNamed("overview").fire("click");
  if (!byId["agent-head"].isConnected) throw new Error("chat pane not restored after admin");
  if (!byId.panel.isConnected) throw new Error("overview panel not restored after admin");
  if (!byId.tabs.isConnected) throw new Error("tabs not restored after admin");
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
