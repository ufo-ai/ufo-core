import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { gzipSync } from "node:zlib";

import {
  FAVICON_DARK_SVG,
  FAVICON_SVG,
  INTER_FONT,
  LANDING_PAGE,
  LEGAL_CSS,
  PRIVACY_DESCRIPTION,
  PRIVACY_PAGE,
  ROBOTO_MONO_FONT,
  SLACK_DESCRIPTION,
  SLACK_PAGE,
  SUBPROCESSORS_DESCRIPTION,
  SUBPROCESSORS_PAGE,
  SUPPORT_DESCRIPTION,
  SUPPORT_PAGE,
  TERMS_DESCRIPTION,
  TERMS_PAGE,
  edgeCache,
  importWorker,
} from "./harness.mjs";

const worker = await importWorker("shared");

const CARD_PATH = "/surface/sites/share/site/site-token/5f2c5f2c.jpg";
const CARD_BYTES = Buffer.from([0xff, 0xd8, 0xff, 0xe0]);
const CARD_CACHE = "public, max-age=600";

const outbound = [];
const outboundRequests = [];
let fleetReply = () => Response.json({ craft: 0 });
let cardReply = () =>
  new Response(CARD_BYTES, {
    headers: { "cache-control": CARD_CACHE, "content-type": "image/jpeg" },
  });
globalThis.fetch = async (input) => {
  const url = input instanceof Request ? input.url : input;
  outbound.push(url);
  outboundRequests.push(input instanceof Request ? input : new Request(input));
  if (url.endsWith("/fleet")) return fleetReply();
  if (url.includes("/surface/sites/share/site/")) return cardReply(url);
  return new Response(`origin:${url}`);
};

const env = { ORIGIN_BASE: "https://testing.flyingobject.ai" };

function request(url, { ua = "curl/8.6.0", method = "GET", body } = {}) {
  return worker.fetch(new Request(url, { method, body, headers: { "user-agent": ua } }), env);
}

test("curl landing renders the card with the mark, the door, and the install command", async () => {
  const reply = await request("https://flyingobject.ai/");
  const body = await reply.text();
  assert.equal(reply.headers.get("content-type"), "text/plain; charset=utf-8");
  assert.match(body, /∵ flyingobject\.ai\n/);
  assert.match(body, /Build the unknown\.\n/);
  assert.match(body, /Sign up: https:\/\/ufo\.ai\/join\/ufo\n/);
  assert.match(body, /Install: curl -fsSL https:\/\/flyingobject\.ai\/ufo \| sh/);
});

test("the card is served with no call and no read of the worker's own", async () => {
  const fresh = await importWorker("card-standalone");
  const before = outbound.length;
  const reply = await fresh.fetch(
    new Request("https://flyingobject.ai/", { headers: { "user-agent": "curl/8.6.0" } }),
    { ORIGIN_BASE: "https://origin.flyingobject.ai" },
  );
  assert.equal(reply.status, 200);
  assert.match(await reply.text(), /∵ flyingobject\.ai/);
  assert.deepEqual(outbound.slice(before), []);
});

test("a card from another door names the production join door", async () => {
  const body = await (await request("https://testing.flyingobject.ai/")).text();
  assert.match(body, /testing\.flyingobject\.ai/);
  assert.match(body, /https:\/\/ufo\.ai\/join\/ufo/);
});

test("curl landing over plain http gets the card directly", async () => {
  const reply = await request("http://flyingobject.ai/");
  assert.equal(reply.status, 200);
  assert.equal(reply.headers.get("content-type"), "text/plain; charset=utf-8");
  assert.match(await reply.text(), /∵ flyingobject\.ai/);
});

test("browser landing over plain http is bounced to https with its query intact", async () => {
  const reply = await request("http://flyingobject.ai/?ref=x", { ua: "Mozilla/5.0" });
  assert.equal(reply.status, 301);
  assert.equal(reply.headers.get("location"), "https://flyingobject.ai/?ref=x");
});

test("the browser page links the product favicon and holds no copy of it", () => {
  assert.match(
    LANDING_PAGE,
    /<link rel="icon" href="\/favicon\.svg" type="image\/svg\+xml" media="\(prefers-color-scheme: light\)" \/>/,
  );
  assert.match(
    LANDING_PAGE,
    /<link rel="icon" href="\/favicon-dark\.svg" type="image\/svg\+xml" media="\(prefers-color-scheme: dark\)" \/>/,
  );
  assert.doesNotMatch(LANDING_PAGE, /data:image\/svg\+xml/);
});

const SHARE_CARD = "https://ufo.ai/share/og-home.jpg";
const SHARE_CARD_ALT =
  "The UFO wordmark in white with three ember dots beside it, centred on a black field.";

function shareTags(page) {
  const tags = {};
  for (const [, kind, name, content] of page.matchAll(
    /<meta (property|name)="((?:og|twitter):[^"]+)" content="([^"]*)" \/>/g,
  )) {
    tags[name] = { kind, content };
  }
  return tags;
}

test("the page hands an unfurler a titled card at an absolute https URL", async () => {
  const tags = shareTags(LANDING_PAGE);
  assert.deepEqual(
    Object.fromEntries(Object.entries(tags).map(([name, { content }]) => [name, content])),
    {
      "og:type": "website",
      "og:site_name": "UFO",
      "og:url": "https://ufo.ai/",
      "og:title": "UFO — Build the unknown.",
      "og:description": "UFO. Build the unknown.",
      "og:image": SHARE_CARD,
      "og:image:width": "1200",
      "og:image:height": "630",
      "og:image:type": "image/jpeg",
      "og:image:alt": SHARE_CARD_ALT,
      "twitter:card": "summary_large_image",
      "twitter:title": "UFO — Build the unknown.",
      "twitter:description": "UFO. Build the unknown.",
      "twitter:image": SHARE_CARD,
    },
  );
  for (const [name, { kind }] of Object.entries(tags)) {
    assert.equal(kind, name.startsWith("og:") ? "property" : "name", name);
  }
  assert.match(LANDING_PAGE, /<title>UFO — Build the unknown\.<\/title>/);
  assert.match(LANDING_PAGE, /<meta name="description" content="UFO\. Build the unknown\." \/>/);
  assert.match(LANDING_PAGE, /<link rel="canonical" href="https:\/\/ufo\.ai\/" \/>/);
  const card = new URL(tags["og:image"].content);
  assert.equal(card.protocol, "https:");
  const committed = await readFile(
    new URL(
      `../../..${card.pathname.replace("/share/", "/servers/control/src/assets/")}`,
      import.meta.url,
    ),
  );
  assert.deepEqual(committed.subarray(0, 3), Buffer.from([0xff, 0xd8, 0xff]));
});

test("a link unfurler is served the page rather than the terminal card", async () => {
  for (const ua of [
    "Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)",
    "Twitterbot/1.0",
    "facebookexternalhit/1.1",
    "LinkedInBot/1.0",
  ]) {
    const reply = await request("https://flyingobject.ai/", { ua });
    assert.equal(reply.headers.get("content-type"), "text/html; charset=utf-8");
    assert.match(await reply.text(), /<meta property="og:image" content="[^"]+" \/>/);
  }
});

test("the card paths are left to the apex origin", async () => {
  for (const path of ["/share/og-home.jpg", "/share/og-site.jpg"]) {
    const reply = await request(`https://flyingobject.ai${path}`, { ua: "Slackbot 1.0" });
    assert.equal(reply.status, 200);
    assert.equal(await reply.text(), `origin:https://flyingobject.ai${path}`);
  }
});

test("a site card is served from the edge cache after one origin read", async () => {
  globalThis.caches = edgeCache();
  const fresh = await importWorker("site-card-hit");
  const ask = () => fresh.fetch(new Request(`https://app.ufo.ai${CARD_PATH}`), env);
  const start = outbound.length;

  const first = await ask();
  const second = await ask();

  for (const reply of [first, second]) {
    assert.equal(reply.status, 200);
    assert.equal(reply.headers.get("content-type"), "image/jpeg");
    assert.equal(reply.headers.get("cache-control"), CARD_CACHE);
    assert.doesNotMatch(reply.headers.get("cache-control"), /immutable/);
    assert.equal(reply.headers.get("set-cookie"), null);
    assert.equal(reply.headers.get("location"), null);
    assert.deepEqual(Buffer.from(await reply.arrayBuffer()), CARD_BYTES);
  }
  assert.deepEqual(outbound.slice(start), [`https://app.ufo.ai${CARD_PATH}`]);
});

test("a refused card is not stored, and no session rides to the origin", async () => {
  globalThis.caches = edgeCache();
  const fresh = await importWorker("site-card-gate");
  const ask = () =>
    fresh.fetch(
      new Request(`https://app.ufo.ai${CARD_PATH}`, {
        headers: { cookie: "ufo_session=member-session", "user-agent": "Slackbot 1.0" },
      }),
      env,
    );
  const start = outbound.length;
  cardReply = () => new Response("Not found", { status: 404 });

  const narrowed = await ask();

  assert.equal(narrowed.status, 404);
  assert.equal(globalThis.caches.stored.size, 0);
  assert.equal(outboundRequests.at(-1).headers.get("cookie"), null);
  cardReply = () =>
    new Response(CARD_BYTES, {
      headers: { "cache-control": CARD_CACHE, "content-type": "image/jpeg" },
    });
  assert.equal((await ask()).status, 200);
  assert.deepEqual(outbound.slice(start), [
    `https://app.ufo.ai${CARD_PATH}`,
    `https://app.ufo.ai${CARD_PATH}`,
  ]);
});

test("the card's cache key is its path alone", async () => {
  globalThis.caches = edgeCache();
  const fresh = await importWorker("site-card-key");
  const start = outbound.length;

  const first = await fresh.fetch(new Request(`https://app.ufo.ai${CARD_PATH}?v=1`), env);
  const second = await fresh.fetch(new Request(`https://app.ufo.ai${CARD_PATH}?v=2`), env);

  assert.equal(first.status, 200);
  assert.equal(second.status, 200);
  assert.deepEqual(Buffer.from(await second.arrayBuffer()), CARD_BYTES);
  assert.deepEqual(outbound.slice(start), [`https://app.ufo.ai${CARD_PATH}`]);
});

test("a write on the card path is passed to the origin and stores nothing", async () => {
  globalThis.caches = edgeCache();
  const fresh = await importWorker("site-card-write");

  const reply = await fresh.fetch(
    new Request(`https://app.ufo.ai${CARD_PATH}`, { method: "POST", body: "x" }),
    env,
  );

  assert.equal(reply.status, 200);
  assert.equal(outboundRequests.at(-1).method, "POST");
  assert.equal(globalThis.caches.stored.size, 0);
});

test("favicons serve the exact light and dark product marks", async () => {
  for (const [path, mark] of [
    ["favicon.svg", FAVICON_SVG],
    ["favicon-dark.svg", FAVICON_DARK_SVG],
  ]) {
    const reply = await request(`https://flyingobject.ai/${path}`, { ua: "Mozilla/5.0" });
    assert.equal(reply.headers.get("content-type"), "image/svg+xml");
    assert.equal(reply.headers.get("cache-control"), "no-cache");
    assert.equal(await reply.text(), mark);
  }
});

test("the public pages serve their house stylesheet and fonts without an origin read", async () => {
  const assets = [
    ["/legal.css", "text/css; charset=utf-8", Buffer.from(LEGAL_CSS)],
    ["/assets/fonts/Inter-VariableFont_wght.woff2", "font/woff2", INTER_FONT],
    ["/assets/fonts/RobotoMono-VariableFont_wght.ttf", "font/ttf", ROBOTO_MONO_FONT],
  ];
  const before = outbound.length;

  for (const [path, type, expected] of assets) {
    const reply = await request(`https://flyingobject.ai${path}`, { ua: "Mozilla/5.0" });
    assert.equal(reply.headers.get("content-type"), type);
    assert.equal(reply.headers.get("cache-control"), "public, max-age=600");
    assert.deepEqual(Buffer.from(await reply.arrayBuffer()), expected);
  }

  assert.deepEqual(outbound.slice(before), []);
});

test("the worker decodes each embedded font before it accepts a request", async () => {
  const source = await readFile(new URL("worker.js", import.meta.url), "utf8");
  const handler = source.slice(source.indexOf("export default"));

  assert.match(source, /const INTER_FONT = base64Bytes\("__INTER_FONT__"\)/);
  assert.match(source, /const ROBOTO_MONO_FONT = base64Bytes\("__ROBOTO_MONO_FONT__"\)/);
  assert.doesNotMatch(handler, /atob|base64Bytes/);
});

test("the public stylesheet uses only the house palette", () => {
  assert.match(LEGAL_CSS, /color-scheme: light dark/);
  assert.deepEqual(
    [...new Set(LEGAL_CSS.match(/#[0-9A-F]{6}/g))].sort(),
    [
      "#0095FF",
      "#191A1A",
      "#262929",
      "#323535",
      "#676767",
      "#919090",
      "#A7A9A9",
      "#EBEAE9",
      "#F4F3F2",
      "#F5F5F5",
      "#FAF9F7",
      "#FF6700",
    ],
  );
});

test("every front door serves the embedded page to browsers", async () => {
  for (const host of ["flyingobject.ai", "testing.flyingobject.ai"]) {
    const fresh = await importWorker(`page-${host}`);
    const reply = await fresh.fetch(
      new Request(`https://${host}/?utm_source=card`, {
        headers: { "user-agent": "Mozilla/5.0" },
      }),
      { ...env, ORIGIN_BASE: `https://origin.${host}` },
    );
    assert.equal(reply.headers.get("content-type"), "text/html; charset=utf-8");
    assert.equal(reply.headers.get("cache-control"), "public, max-age=600");
    assert.equal(await reply.text(), LANDING_PAGE);
  }
});

test("the browser page is served with no call of the worker's own", async () => {
  const fresh = await importWorker("page-standalone");
  fleetReply = () => {
    throw new Error("origin down");
  };
  const before = outbound.length;
  const reply = await fresh.fetch(
    new Request("https://flyingobject.ai/", { headers: { "user-agent": "Mozilla/5.0" } }),
    env,
  );
  assert.equal(reply.status, 200);
  assert.equal(await reply.text(), LANDING_PAGE);
  assert.deepEqual(outbound.slice(before), []);
  fleetReply = () => Response.json({ craft: 0 });
});

const HEAD_BUDGET = 96 * 1024;
const PAGE_BUDGET = 320 * 1024;

test("the served page stays inside its transfer budget", () => {
  const transferred = (text) => gzipSync(Buffer.from(text), { level: 6 }).byteLength;
  const head = LANDING_PAGE.slice(0, LANDING_PAGE.indexOf("<body>"));
  assert.ok(
    transferred(head) <= HEAD_BUDGET,
    `${transferred(head)} bytes reach the browser before <body>, over ${HEAD_BUDGET}`,
  );
  assert.ok(
    transferred(LANDING_PAGE) <= PAGE_BUDGET,
    `the page transfers ${transferred(LANDING_PAGE)} bytes, over ${PAGE_BUDGET}`,
  );
});

test("the served page fits the device without taking pinch zoom away", () => {
  assert.match(
    LANDING_PAGE,
    /<meta name="viewport" content="width=device-width, initial-scale=1" \/>/,
  );
  assert.doesNotMatch(LANDING_PAGE, /(?:maximum|minimum)-scale|user-scalable/);
});

test("the worker uses its configured environment origin", async () => {
  const doors = [
    ["prod", "flyingobject.ai", "https://origin.flyingobject.ai"],
    ["testing", "testing.flyingobject.ai", "https://origin.testing.flyingobject.ai"],
    ["binding", "door.example", "https://origin.example"],
  ];
  for (const [name, host, origin] of doors) {
    const fresh = await importWorker(`door-${name}`);
    const start = outbound.length;
    const doorEnv = { ...env, ORIGIN_BASE: origin };
    await fresh.fetch(
      new Request(`https://${host}/`, { headers: { "user-agent": "Mozilla/5.0" } }),
      doorEnv,
    );
    const installer = await fresh.fetch(new Request(`https://${host}/ufo`), doorEnv);
    const binary = await fresh.fetch(
      new Request(`https://${host}/ufo/bin/aarch64-apple-darwin`),
      doorEnv,
    );
    const fleet = await fresh.fetch(new Request(`https://${host}/fleet`), doorEnv);
    assert.equal(await installer.text(), `origin:${origin}/ufo`);
    assert.equal(await binary.text(), `origin:${origin}/ufo/bin/aarch64-apple-darwin`);
    assert.deepEqual(await fleet.json(), { craft: 0 });
    assert.deepEqual(outbound.slice(start), [
      `${origin}/ufo`,
      `${origin}/ufo/bin/aarch64-apple-darwin`,
      `${origin}/fleet`,
    ]);
  }
});

test("onboarding posts to the configured gateway origin unchanged", async () => {
  const reply = await worker.fetch(
    new Request("https://flyingobject.ai/v1/onboard/ufo?step=email", {
      method: "POST",
      body: "member@example.com",
      headers: { "x-ufo-session": "member-session" },
    }),
    env,
  );
  const forwarded = outboundRequests.at(-1);
  assert.equal(
    await reply.text(),
    "origin:https://testing.flyingobject.ai/v1/onboard/ufo?step=email",
  );
  assert.equal(
    forwarded.url,
    "https://testing.flyingobject.ai/v1/onboard/ufo?step=email",
  );
  assert.equal(forwarded.method, "POST");
  assert.equal(forwarded.headers.get("x-ufo-session"), "member-session");
  assert.equal(await forwarded.text(), "member@example.com");
});

test("any other path passes through untouched", async () => {
  const reply = await request("https://flyingobject.ai/status", { ua: "Mozilla/5.0" });
  assert.equal(await reply.text(), "origin:https://flyingobject.ai/status");
});

test("apex /login 302s to the app host, the sole authenticated origin", async () => {
  const prod = await request("https://ufo.ai/login", { ua: "Mozilla/5.0" });
  assert.equal(prod.status, 302);
  assert.equal(prod.headers.get("location"), "https://app.ufo.ai/login");
  const testing = await request("https://testing.ufo.ai/login", { ua: "Mozilla/5.0" });
  assert.equal(testing.headers.get("location"), "https://app.testing.ufo.ai/login");
});

test("an apex door carries the ask it was opened with to the host that reads it", async () => {
  const invited = await request("https://ufo.ai/login?invite=1", { ua: "Mozilla/5.0" });
  assert.equal(invited.status, 302);
  assert.equal(invited.headers.get("location"), "https://app.ufo.ai/login?invite=1");
  const named = await request("https://ufo.ai/login?c=6f1c8038-1111-4222-8333-444455556666", {
    ua: "Mozilla/5.0",
  });
  assert.equal(
    named.headers.get("location"),
    "https://app.ufo.ai/login?c=6f1c8038-1111-4222-8333-444455556666",
  );
});

test("apex /join carries the signup key to the app host that answers it", async () => {
  const prod = await request("https://ufo.ai/join/ufo", { ua: "Mozilla/5.0" });
  assert.equal(prod.status, 302);
  assert.equal(prod.headers.get("location"), "https://app.ufo.ai/join/ufo");
  const testing = await request("https://testing.ufo.ai/join/61fcacb5", { ua: "Mozilla/5.0" });
  assert.equal(testing.headers.get("location"), "https://app.testing.ufo.ai/join/61fcacb5");
});

test("apex /logout 302s to the app host, where the session cookie is bound", async () => {
  const prod = await request("https://ufo.ai/logout", { ua: "Mozilla/5.0" });
  assert.equal(prod.status, 302);
  assert.equal(prod.headers.get("location"), "https://app.ufo.ai/logout");
  const testing = await request("https://testing.ufo.ai/logout", { ua: "Mozilla/5.0" });
  assert.equal(testing.headers.get("location"), "https://app.testing.ufo.ai/logout");
});

test("/ufo serves byte-identical content to every user agent", async () => {
  const cli = await (await request("https://flyingobject.ai/ufo")).text();
  const browser = await (
    await request("https://flyingobject.ai/ufo", { ua: "Mozilla/5.0" })
  ).text();
  assert.equal(cli, browser);
});

const PUBLIC_PAGES = [
  {
    path: "/privacy",
    document: PRIVACY_PAGE,
    title: "Privacy Policy",
    description: PRIVACY_DESCRIPTION,
    canonical: "https://ufo.ai/privacy",
    headings: [
      "1. Information We Collect",
      "2. How We Use Information",
      "3. AI Processing",
      "4. Google API Services User Data Policy",
      "5. Data Sharing",
      "6. Data Retention and Your Choices",
      "7. Children's Privacy",
      "8. Changes to This Policy",
      "9. Contact Us",
    ],
  },
  {
    path: "/slack",
    document: SLACK_PAGE,
    title: "ufo for Slack",
    description: SLACK_DESCRIPTION,
    canonical: "https://ufo.ai/slack",
    headings: [
      "What ufo does",
      "Permissions",
      "Install ufo",
      "Use ufo",
      "AI output",
      "Privacy and support",
    ],
  },
  {
    path: "/subprocessors",
    document: SUBPROCESSORS_PAGE,
    title: "Subprocessors",
    description: SUBPROCESSORS_DESCRIPTION,
    canonical: "https://ufo.ai/subprocessors",
    headings: [
      "Infrastructure",
      "AI and search",
      "Tools and connections",
      "Accounts and operations",
      "Changes",
      "Contact",
    ],
  },
  {
    path: "/support",
    document: SUPPORT_PAGE,
    title: "Support",
    description: SUPPORT_DESCRIPTION,
    canonical: "https://ufo.ai/support",
    headings: ["Contact", "What to include", "Privacy requests"],
  },
  {
    path: "/terms",
    document: TERMS_PAGE,
    title: "Terms of Service",
    description: TERMS_DESCRIPTION,
    canonical: "https://ufo.ai/terms",
    headings: [
      "1. Use of the Service",
      "2. Accounts",
      "3. Acceptable Use",
      "4. Intellectual Property",
      "5. Termination",
      "6. Disclaimer of Warranties",
      "7. Limitation of Liability",
      "8. Changes to These Terms",
      "9. Governing Law",
      "10. Contact Us",
    ],
  },
];

test("each public page is served whole, headed by its own title", async () => {
  for (const page of PUBLIC_PAGES) {
    const reply = await request(`https://flyingobject.ai${page.path}`, { ua: "Mozilla/5.0" });
    assert.equal(reply.status, 200);
    assert.equal(reply.headers.get("content-type"), "text/html; charset=utf-8");
    assert.equal(reply.headers.get("cache-control"), "public, max-age=600");
    const served = await reply.text();
    assert.equal(served, page.document);
    assert.doesNotMatch(served, /__[A-Z_]+__/);
    assert.match(served, new RegExp(`<title>${page.title}</title>`));
    assert.match(served, new RegExp(`<h1>${page.title}</h1>`));
    assert.match(served, /<link rel="stylesheet" href="\/legal\.css" \/>/);
    assert.match(served, /<header class="site-header">/);
    assert.match(served, /<nav class="site-nav" aria-label="Public pages">/);
    assert.deepEqual(
      [...served.matchAll(/<h2>([^<]+)<\/h2>/g)].map(([, heading]) => heading),
      page.headings,
    );
    assert.match(served, /support@ufo\.ai/);
    assert.doesNotMatch(served, /founders@metalcraft\.ai/);
    assert.match(served, /ufo\.ai/);
    assert.doesNotMatch(served, /Flying Object AI/);
    assert.doesNotMatch(served, /flyingobject\.ai/);
    assert.match(
      served,
      /Slack is a trademark and service mark of Slack Technologies, Inc\., registered in the U\.S\. and in other countries\./,
    );
    assert.match(served, /Copyright 2023 Slack Technologies, LLC\./);
  }
});

test("a public page reads the same for every user agent", async () => {
  for (const { path } of PUBLIC_PAGES) {
    const cli = await (await request(`https://flyingobject.ai${path}`)).text();
    const browser = await (
      await request(`https://flyingobject.ai${path}`, { ua: "Mozilla/5.0" })
    ).text();
    assert.equal(cli, browser);
  }
});

test("a public page over plain http is bounced to https with its query intact", async () => {
  for (const { path } of PUBLIC_PAGES) {
    const reply = await request(`http://flyingobject.ai${path}?ref=x`, { ua: "Mozilla/5.0" });
    assert.equal(reply.status, 301);
    assert.equal(reply.headers.get("location"), `https://flyingobject.ai${path}?ref=x`);
  }
});

test("the home page does not link to a secondary public page", async () => {
  const card = await (await request("https://flyingobject.ai/")).text();
  for (const { path } of PUBLIC_PAGES) {
    assert.doesNotMatch(LANDING_PAGE, new RegExp(path));
    assert.doesNotMatch(card, new RegExp(path));
  }
});

test("each public page carries its own description, canonical URL, and share tags", async () => {
  for (const legal of PUBLIC_PAGES) {
    const served = await (
      await request(`https://flyingobject.ai${legal.path}`, { ua: "Mozilla/5.0" })
    ).text();
    assert.match(served, new RegExp(`<meta name="description" content="${legal.description}" />`));
    assert.match(served, new RegExp(`<link rel="canonical" href="${legal.canonical}" />`));
    const tags = shareTags(served);
    assert.deepEqual(
      Object.fromEntries(Object.entries(tags).map(([name, { content }]) => [name, content])),
      {
        "og:type": "website",
        "og:site_name": "UFO",
        "og:url": legal.canonical,
        "og:title": legal.title,
        "og:description": legal.description,
        "og:image": SHARE_CARD,
        "og:image:width": "1200",
        "og:image:height": "630",
        "og:image:type": "image/jpeg",
        "og:image:alt": SHARE_CARD_ALT,
        "twitter:card": "summary_large_image",
        "twitter:title": legal.title,
        "twitter:description": legal.description,
        "twitter:image": SHARE_CARD,
      },
    );
    for (const [name, { kind }] of Object.entries(tags)) {
      assert.equal(kind, name.startsWith("og:") ? "property" : "name", name);
    }
  }
});

const locations = (xml) => [...xml.matchAll(/<loc>([^<]+)<\/loc>/g)].map(([, loc]) => loc);
const refusals = (rules) => [...rules.matchAll(/^Disallow: (\S+)$/gm)].map(([, path]) => path);

const INDEXED = ["https://ufo.ai/", ...PUBLIC_PAGES.map(({ canonical }) => canonical)];

test("the sitemap lists the home page and each public page at its canonical URL", async () => {
  const reply = await request("https://flyingobject.ai/sitemap.xml", { ua: "Googlebot/2.1" });
  assert.equal(reply.status, 200);
  assert.equal(reply.headers.get("content-type"), "application/xml; charset=utf-8");
  assert.equal(reply.headers.get("cache-control"), "public, max-age=600");
  const served = await reply.text();
  assert.match(served, /^<\?xml version="1\.0" encoding="UTF-8"\?>\n/);
  assert.match(served, /<urlset xmlns="http:\/\/www\.sitemaps\.org\/schemas\/sitemap\/0\.9">/);
  assert.deepEqual(locations(served), INDEXED);
});

test("the Slack page gives the install path, first use, AI warning, and policy links", async () => {
  assert.match(SLACK_PAGE, /href="\/login">Sign in<\/a>/);
  assert.match(SLACK_PAGE, /href="\/login\?signup=1">create a workspace or join the waitlist<\/a>/);
  assert.match(SLACK_PAGE, /ask ufo to connect a Slack workspace/);
  assert.match(SLACK_PAGE, /Add to Slack/);
  assert.match(SLACK_PAGE, /direct message/);
  assert.match(SLACK_PAGE, /can be inaccurate/);
  assert.match(SLACK_PAGE, /href="\/privacy">Privacy Policy<\/a>/);
  assert.match(SLACK_PAGE, /href="\/support">Support<\/a>/);
});

test("the Slack page explains its thread behavior and permissions", async () => {
  assert.match(SLACK_PAGE, /ufo does not join channels on its own/);
  assert.match(SLACK_PAGE, /can\s+answer later messages in that thread when they are relevant/);
  assert.match(SLACK_PAGE, /Message history: reads the channel, direct-message, and thread context/);
  assert.match(SLACK_PAGE, /ufo uses a bot token\. It does not request a Slack user token\./);
});

test("the policy states Slack data rights, retention, no LLM training, and minimum age", async () => {
  assert.match(PRIVACY_PAGE, /Slack messages, files, comments, profile information,\s+metadata/);
  assert.match(PRIVACY_PAGE, /access, transfer, correction, or deletion/);
  assert.match(PRIVACY_PAGE, /while the related ufo workspace is active and as needed to\s+provide/);
  assert.match(PRIVACY_PAGE, /do not use Slack data to train/);
  assert.match(PRIVACY_PAGE, /discard those unused\s+fields after we process the Slack event/);
  assert.match(PRIVACY_PAGE, /ufo for Slack does not permit use by children under 16/);
  assert.match(TERMS_PAGE, /at least 16 years old to use ufo for\s+Slack/);
});

test("the privacy policy links to the named providers and their work", () => {
  assert.match(PRIVACY_PAGE, /href="\/subprocessors">Subprocessor List<\/a>/);
  for (const provider of [
    "Amazon Web Services",
    "Cloudflare",
    "OpenRouter",
    "Fireworks AI",
    "Baseten",
    "OpenAI",
    "Perplexity",
    "Turbopuffer",
    "E2B",
    "Browserbase",
    "Composio",
    "Pipedream",
    "WorkOS",
    "People Data Labs",
    "Stripe",
    "Metronome",
    "Datadog",
  ]) {
    assert.match(SUBPROCESSORS_PAGE, new RegExp(provider));
  }
});

test("every page the sitemap lists is served as a document", async () => {
  for (const location of INDEXED) {
    const reply = await request(`https://flyingobject.ai${new URL(location).pathname}`, {
      ua: "Mozilla/5.0",
    });
    assert.equal(reply.status, 200);
    assert.equal(reply.headers.get("content-type"), "text/html; charset=utf-8");
  }
});

test("robots.txt opens the pages, refuses the endpoints, and names the sitemap", async () => {
  const reply = await request("https://flyingobject.ai/robots.txt", { ua: "Googlebot/2.1" });
  assert.equal(reply.status, 200);
  assert.equal(reply.headers.get("content-type"), "text/plain; charset=utf-8");
  assert.equal(reply.headers.get("cache-control"), "public, max-age=600");
  const served = await reply.text();
  assert.match(served, /^User-agent: \*\nAllow: \/\n/);
  assert.deepEqual(refusals(served), ["/ufo", "/fleet", "/v1/onboard/", "/login"]);
  assert.match(served, /^Sitemap: https:\/\/ufo\.ai\/sitemap\.xml$/m);
  assert.match(served, /^Sitemap: https:\/\/ufo\.ai\/sitemap-index\.xml$/m);
});

test("no page the sitemap lists is a path robots.txt refuses", async () => {
  const refused = refusals(await (await request("https://flyingobject.ai/robots.txt")).text());
  for (const location of INDEXED) {
    const { pathname } = new URL(location);
    assert.deepEqual(
      refused.filter((path) => pathname.startsWith(path)),
      [],
      pathname,
    );
  }
});

test("the crawl files over plain http are bounced to https", async () => {
  for (const path of ["/robots.txt", "/sitemap.xml"]) {
    const reply = await request(`http://flyingobject.ai${path}`, { ua: "Googlebot/2.1" });
    assert.equal(reply.status, 301);
    assert.equal(reply.headers.get("location"), `https://flyingobject.ai${path}`);
  }
});

const BANNED_LEXICON =
  /!|\bwelcome\b|\boops\b|\bjust\b|\bsimply\b|\bawesome\b|\bboard(ing)?\b|\bpassengers?\b|\bshortly\b|\bsoon\b|\brecently\b|you'?re all set/i;

const BANNED_METAPHOR =
  /\bbeam\w*|\btransmit\w*|\bsignals?\b|\bsaucers?\b|\bmothership\b|\bcraft\b|\bfleets?\b|\babduct\w*|\b(un)?identified\b|\bidentification\b|\bobjects?\b/i;

function assertStandardCase(surface) {
  for (const line of surface.split("\n")) {
    for (const sentence of line.split(/[.?!]\s+/)) {
      const opener = sentence.split(/\s+/).find((word) => /[a-z0-9]/i.test(word));
      if (!opener || /^(?:curl|ufo|x-ufo-|\S*[.@]\S+)/.test(opener)) continue;
      const firstAlnum = opener.match(/[a-z0-9]/i)[0];
      assert.ok(
        !/[a-z]/.test(firstAlnum),
        `lowercase sentence start "${opener}" in "${line.trim()}"`,
      );
    }
  }
}

test("the case gate anchors every sentence and sees past glyphs", () => {
  for (const styled of [
    "Done. try again.",
    "Sent! check your inbox.",
    "✓ installed ufo",
    "› type ufo to continue.",
    "✓installed ufo",
    "(optional) set your name.",
  ]) {
    assert.throws(() => assertStandardCase(styled), undefined, styled);
  }
  for (const plain of [
    "✓ Installed ufo (/x/bin/ufo)",
    "gmail.com is not a work email domain.",
    "Gmail.com is not a work email domain.",
    "Read README.md for the format.",
    "Node.js is required.",
    "Done.try again",
    "Sent!check your inbox.",
    "Done?try again",
    "curl -fsSL https://flyingobject.ai/ufo | sh",
  ]) {
    assertStandardCase(plain);
  }
});

test("member-facing surfaces carry no banned lexicon and no ufo metaphor", async () => {
  const card = await (await request("https://flyingobject.ai/")).text();
  assert.doesNotMatch(card, BANNED_LEXICON);
  assert.doesNotMatch(card, BANNED_METAPHOR);
  assertStandardCase(card);
});

test("the apex offers no waitlist door", async () => {
  const posted = await request("https://flyingobject.ai/waitlist", {
    method: "POST",
    body: "email=pilot@example.com",
  });
  assert.equal(posted.status, 200);
  assert.deepEqual(outbound.slice(-1), ["https://flyingobject.ai/waitlist"]);

  const card = await (await request("https://flyingobject.ai/")).text();
  assert.doesNotMatch(card, /waitlist/i);
});
