import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";
import { gzipSync } from "node:zlib";

import {
  FAVICON_DARK_SVG,
  FAVICON_SVG,
  LANDING_PAGE,
  PRIVACY_PAGE,
  TERMS_PAGE,
  d1,
  importWorker,
} from "./harness.mjs";

const worker = await importWorker("shared");

const EMAIL_LEDGER =
  "create table if not exists waitlist_email (email text primary key, queued_at text, sent_at text)";

const outbound = [];
const outboundRequests = [];
let fleetReply = () => Response.json({ craft: 0 });
globalThis.fetch = async (input) => {
  const url = input instanceof Request ? input.url : input;
  outbound.push(url);
  outboundRequests.push(input instanceof Request ? input : new Request(input));
  if (url.endsWith("/fleet")) return fleetReply();
  return new Response(`origin:${url}`);
};

const env = {
  DB: d1(),
  ORIGIN_BASE: "https://testing.flyingobject.ai",
  WAITLIST_EMAILS: { async send() {} },
  WAITLIST_DEAD_LETTER_QUEUE: "ufo-edge-waitlist-email-dead-letters",
};

function request(url, { ua = "curl/8.6.0", method = "GET", body } = {}) {
  return worker.fetch(new Request(url, { method, body, headers: { "user-agent": ua } }), env);
}

test("curl landing renders the card with live counts and https commands", async () => {
  const reply = await request("https://flyingobject.ai/");
  const body = await reply.text();
  assert.equal(reply.headers.get("content-type"), "text/plain; charset=utf-8");
  assert.match(body, /◉ ◉ ◉/);
  assert.match(body, /flyingobject\.ai/);
  assert.match(body, /4 workspaces\. 0 on the waitlist\./);
  assert.match(body, /Join the waitlist:/);
  assert.match(body, /curl https:\/\/flyingobject\.ai\/waitlist -d email=/);
  assert.match(body, /curl -fsSL https:\/\/flyingobject\.ai\/ufo \| sh/);
});

test("curl landing over plain http gets the card directly", async () => {
  const reply = await request("http://flyingobject.ai/");
  assert.equal(reply.status, 200);
  assert.equal(reply.headers.get("content-type"), "text/plain; charset=utf-8");
  assert.match(await reply.text(), /\d+ workspaces?\. \d+ on the waitlist\./);
});

test("browser landing over plain http is bounced to https with its query intact", async () => {
  const reply = await request("http://flyingobject.ai/?ref=x", { ua: "Mozilla/5.0" });
  assert.equal(reply.status, 301);
  assert.equal(reply.headers.get("location"), "https://flyingobject.ai/?ref=x");
});

// The mark has one home in this repo. The page links the worker's routes rather than carrying a
// copy, so a mark that changes on disk changes on the tab.
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

// What an unfurler reads: the tags, and then one absolute URL it fetches with no session. Every
// value it draws is held here, because a card is invisible in the product and only ever seen in
// somebody else's Slack.
const SHARE_CARD = "https://ufo.ai/share/og-home.jpg";

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
      "og:title": "UFO — Go further.",
      "og:description": "UFO. Go further.",
      "og:image": SHARE_CARD,
      "og:image:width": "1200",
      "og:image:height": "630",
      "og:image:type": "image/jpeg",
      "og:image:alt": "Go further. — three figures under a dark sky, the UFO mark above them.",
      "twitter:card": "summary_large_image",
      "twitter:title": "UFO — Go further.",
      "twitter:description": "UFO. Go further.",
      "twitter:image": SHARE_CARD,
    },
  );
  // The `og:` names are properties and the `twitter:` ones are names; a tag written the other way
  // is dropped by the crawler that reads it.
  for (const [name, { kind }] of Object.entries(tags)) {
    assert.equal(kind, name.startsWith("og:") ? "property" : "name", name);
  }
  // The unfurl and the tab must say the same thing, and the page keeps one canonical home.
  assert.match(LANDING_PAGE, /<title>UFO — Go further\.<\/title>/);
  assert.match(LANDING_PAGE, /<meta name="description" content="UFO\. Go further\." \/>/);
  assert.match(LANDING_PAGE, /<link rel="canonical" href="https:\/\/ufo\.ai\/" \/>/);
  // Absolute and https, because a crawler resolves it against nothing.
  const card = new URL(tags["og:image"].content);
  assert.equal(card.protocol, "https:");
  // And it is the card this repository holds, where the gateway compiles it in from.
  const committed = await readFile(
    new URL(`../../..${card.pathname.replace("/share/", "/control/src/assets/")}`, import.meta.url),
  );
  assert.deepEqual(committed.subarray(0, 3), Buffer.from([0xff, 0xd8, 0xff]));
});

// A crawler is not a CLI: the text card would unfurl as a wall of ASCII, so the unfurlers get the
// document with the tags in it.
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

// The cards themselves are the gateway's, compiled in beside /login/logo.png. The worker claims no
// path under /share/, so both fall through to the apex origin that serves them.
test("the card paths are left to the apex origin", async () => {
  for (const path of ["/share/og-home.jpg", "/share/og-site.jpg"]) {
    const reply = await request(`https://flyingobject.ai${path}`, { ua: "Slackbot 1.0" });
    assert.equal(reply.status, 200);
    assert.equal(await reply.text(), `origin:https://flyingobject.ai${path}`);
  }
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

// The page names no host, so every front door serves the one document.
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

// The page stands whole in itself, so a gateway that is down takes nothing away from it.
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

test("signup is positional, idempotent, and normalizes the email", async () => {
  const first = await request("https://flyingobject.ai/waitlist", {
    method: "POST",
    body: "email=You@YourCo.com",
  });
  assert.equal(first.status, 200);
  assert.match(await first.text(), /#1 on the waitlist\. We will email you when access opens\./);
  const second = await request("https://flyingobject.ai/waitlist", {
    method: "POST",
    body: "email=second@co.com&junk=1",
  });
  assert.match(await second.text(), /#2 on the waitlist\./);
  const duplicate = await request("https://flyingobject.ai/waitlist", {
    method: "POST",
    body: "email=you@yourco.com",
  });
  assert.match(await duplicate.text(), /#1 on the waitlist\./);
});

// A visitor reads nothing until the bytes above <body> arrive, and pays for the whole document
// once per cache lifetime. Both are bounded here so an inlined asset cannot quietly restore the
// weight: the payload the apex serves is the one thing on it a member cannot work around.
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
  // Pinning the scale is the usual way to stop Safari's focus zoom. It strips zoom from every
  // visitor and modern Safari ignores it anyway, so the controls carry 16px instead.
  assert.doesNotMatch(LANDING_PAGE, /(?:maximum|minimum)-scale|user-scalable/);
});

test("a first join queues and delivers one confirmation email", async () => {
  const queued = [];
  const sent = [];
  const isolated = {
    DB: d1(),
    ORIGIN_BASE: "https://testing.flyingobject.ai",
    WAITLIST_DEAD_LETTER_QUEUE: "ufo-edge-waitlist-email-dead-letters",
    WAITLIST_EMAILS: {
      async send(message, options) {
        queued.push({ message, options });
      },
    },
    EMAIL: {
      async send(message) {
        sent.push(message);
      },
    },
  };
  const fresh = await importWorker("waitlist-email");
  const signup = () =>
    new Request("https://flyingobject.ai/waitlist", {
      method: "POST",
      body: "email=Pilot@Example.com",
      headers: { "user-agent": "curl/8.6.0" },
    });
  await fresh.fetch(signup(), isolated);
  await fresh.fetch(signup(), isolated);
  assert.deepEqual(queued, [
    { message: { email: "pilot@example.com" }, options: { contentType: "json" } },
  ]);

  let acknowledged = false;
  await fresh.queue(
    {
      queue: "ufo-edge-waitlist-email",
      messages: [
        {
          body: queued[0].message,
          ack() {
            acknowledged = true;
          },
        },
      ],
    },
    isolated,
  );
  assert.deepEqual(sent, [
    {
      to: "pilot@example.com",
      from: "no-reply@flyingobject.ai",
      subject: "#1 on the ufo waitlist",
      text:
        "pilot@example.com is #1 on the waitlist. We will email you when access opens.\n",
    },
  ]);
  assert.equal(acknowledged, true);

  await fresh.fetch(signup(), isolated);
  assert.equal(queued.length, 1);
});

test("a failed confirmation remains unacknowledged for queue retry", async () => {
  const database = d1();
  await database.prepare(EMAIL_LEDGER).run();
  await database
    .prepare("insert into waitlist_email (email) values (?1) on conflict do nothing")
    .bind("pilot@example.com")
    .run();
  database.database.exec(
    "create table waitlist (n integer primary key autoincrement," +
    " email text not null unique, created_at text not null default (datetime('now')))",
  );
  database.database.prepare("insert into waitlist (email) values (?)").run("pilot@example.com");
  let acknowledged = false;
  await assert.rejects(
    worker.queue(
      {
        queue: "ufo-edge-waitlist-email",
        messages: [
          {
            body: { email: "pilot@example.com" },
            ack() {
              acknowledged = true;
            },
          },
        ],
      },
      {
        DB: database,
        EMAIL: {
          async send() {
            throw new Error("email unavailable");
          },
        },
        WAITLIST_DEAD_LETTER_QUEUE: "ufo-edge-waitlist-email-dead-letters",
      },
    ),
    /email unavailable/,
  );
  assert.equal(acknowledged, false);
});

test("a signup retries an unqueued confirmation", async () => {
  const queued = [];
  let attempt = 0;
  const isolated = {
    DB: d1(),
    ORIGIN_BASE: "https://testing.flyingobject.ai",
    WAITLIST_DEAD_LETTER_QUEUE: "ufo-edge-waitlist-email-dead-letters",
    WAITLIST_EMAILS: {
      async send(message, options) {
        attempt += 1;
        if (attempt === 1) throw new Error("queue unavailable");
        queued.push({ message, options });
      },
    },
  };
  const fresh = await importWorker("waitlist-email-retry");
  const signup = () =>
    new Request("https://flyingobject.ai/waitlist", {
      method: "POST",
      body: "email=Pilot@Example.com",
      headers: { "user-agent": "curl/8.6.0" },
    });

  await assert.rejects(fresh.fetch(signup(), isolated), /queue unavailable/);
  const reply = await fresh.fetch(signup(), isolated);
  assert.equal(reply.status, 200);
  assert.deepEqual(queued, [
    { message: { email: "pilot@example.com" }, options: { contentType: "json" } },
  ]);
});

test("an exhausted confirmation is surfaced and consumed", async (context) => {
  const logged = context.mock.method(console, "error", () => {});
  const database = d1();
  await database.prepare(EMAIL_LEDGER).run();
  await database
    .prepare("insert into waitlist_email (email) values (?1) on conflict do nothing")
    .bind("pilot@example.com")
    .run();
  await database
    .prepare("update waitlist_email set queued_at = datetime('now') where email = ?1")
    .bind("pilot@example.com")
    .run();
  let acknowledged = false;
  await worker.queue(
    {
      queue: "ufo-edge-waitlist-email-dead-letters",
      messages: [
        {
          body: { email: "pilot@example.com" },
          ack() {
            acknowledged = true;
          },
        },
      ],
    },
    {
      DB: database,
      WAITLIST_DEAD_LETTER_QUEUE: "ufo-edge-waitlist-email-dead-letters",
    },
  );
  assert.equal(acknowledged, true);
  assert.deepEqual(logged.mock.calls[0].arguments, [
    "waitlist confirmation failed for pilot@example.com",
  ]);
  assert.deepEqual(
    await database
      .prepare("select queued_at, sent_at from waitlist_email where email = ?1")
      .bind("pilot@example.com")
      .first(),
    { queued_at: null, sent_at: null },
  );
});

test("a signup busts the counter cache so the card reflects it", async () => {
  const body = await (await request("https://flyingobject.ai/")).text();
  assert.match(body, /4 workspaces\. 2 on the waitlist\./);
});

test("a malformed email is a 400 and takes no queue slot", async () => {
  const reply = await request("https://flyingobject.ai/waitlist", {
    method: "POST",
    body: "email=nope",
  });
  assert.equal(reply.status, 400);
  assert.match(await reply.text(), /curl https:\/\/flyingobject\.ai\/waitlist/);
  const next = await request("https://flyingobject.ai/waitlist", {
    method: "POST",
    body: "email=third@co.com",
  });
  assert.match(await next.text(), /#3 on the waitlist\./);
});

test("GET /waitlist answers with usage for the requested host", async () => {
  const reply = await request("https://testing.flyingobject.ai/waitlist");
  assert.match(await reply.text(), /curl https:\/\/testing\.flyingobject\.ai\/waitlist/);
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

test("/ufo serves byte-identical content to every user agent", async () => {
  const cli = await (await request("https://flyingobject.ai/ufo")).text();
  const browser = await (
    await request("https://flyingobject.ai/ufo", { ua: "Mozilla/5.0" })
  ).text();
  assert.equal(cli, browser);
});

const LEGAL = [
  {
    path: "/privacy",
    document: PRIVACY_PAGE,
    title: "Privacy Policy",
    headings: [
      "1. Information We Collect",
      "2. How We Use Information",
      "3. Google API Services User Data Policy",
      "4. Data Sharing",
      "5. Data Retention",
      "6. Children's Privacy",
      "7. Changes to This Policy",
      "8. Contact Us",
    ],
  },
  {
    path: "/terms",
    document: TERMS_PAGE,
    title: "Terms of Service",
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

test("each legal page is served whole, headed by its own title", async () => {
  for (const legal of LEGAL) {
    const reply = await request(`https://flyingobject.ai${legal.path}`, { ua: "Mozilla/5.0" });
    assert.equal(reply.status, 200);
    assert.equal(reply.headers.get("content-type"), "text/html; charset=utf-8");
    assert.equal(reply.headers.get("cache-control"), "public, max-age=600");
    const served = await reply.text();
    assert.equal(served, legal.document);
    assert.doesNotMatch(served, /__[A-Z_]+__/);
    assert.match(served, new RegExp(`<title>${legal.title}</title>`));
    assert.match(served, new RegExp(`<h1>${legal.title}</h1>`));
    assert.deepEqual(
      [...served.matchAll(/<h2>([^<]+)<\/h2>/g)].map(([, heading]) => heading),
      legal.headings,
    );
    assert.match(served, /founders@metalcraft\.ai/);
    assert.match(served, /ufo\.ai/);
    assert.doesNotMatch(served, /Flying Object AI/);
    assert.doesNotMatch(served, /flyingobject\.ai/);
  }
});

test("a legal page reads the same for every user agent", async () => {
  for (const { path } of LEGAL) {
    const cli = await (await request(`https://flyingobject.ai${path}`)).text();
    const browser = await (
      await request(`https://flyingobject.ai${path}`, { ua: "Mozilla/5.0" })
    ).text();
    assert.equal(cli, browser);
  }
});

test("a legal page over plain http is bounced to https with its query intact", async () => {
  for (const { path } of LEGAL) {
    const reply = await request(`http://flyingobject.ai${path}?ref=x`, { ua: "Mozilla/5.0" });
    assert.equal(reply.status, 301);
    assert.equal(reply.headers.get("location"), `https://flyingobject.ai${path}?ref=x`);
  }
});

// Google's consent screen reads these pages by URL. Nothing the member sees leads to them.
test("no public surface links to a legal page", async () => {
  const card = await (await request("https://flyingobject.ai/")).text();
  for (const { path } of LEGAL) {
    assert.doesNotMatch(LANDING_PAGE, new RegExp(path));
    assert.doesNotMatch(card, new RegExp(path));
  }
});

test("an unnumbered waitlist is renumbered once, in insertion order, permanently", async () => {
  const database = new DatabaseSync(":memory:");
  database.exec(
    "create table waitlist (email text primary key," +
    " created_at text not null default (datetime('now')))",
  );
  const seed = database.prepare("insert into waitlist (email, created_at) values (?, ?)");
  seed.run("first@co.com", "2026-07-01 00:00:00");
  seed.run("second@co.com", "2026-07-01 00:00:00");
  const isolated = {
    DB: d1(database),
    ORIGIN_BASE: "https://testing.flyingobject.ai",
    WAITLIST_DEAD_LETTER_QUEUE: "ufo-edge-waitlist-email-dead-letters",
    WAITLIST_EMAILS: { async send() {} },
  };
  const fresh = await importWorker("waitlist-migrate");
  const signup = (email) =>
    fresh.fetch(
      new Request("https://flyingobject.ai/waitlist", {
        method: "POST",
        body: `email=${email}`,
        headers: { "user-agent": "curl/8.6.0" },
      }),
      isolated,
    );

  const third = await signup("third@co.com");
  assert.match(await third.text(), /#3 on the waitlist\./);
  const numbers = database
    .prepare("select email, n from waitlist order by n")
    .all()
    .map(({ email, n }) => ({ email, n }));
  assert.deepEqual(numbers, [
    { email: "first@co.com", n: 1 },
    { email: "second@co.com", n: 2 },
    { email: "third@co.com", n: 3 },
  ]);

  database.prepare("delete from waitlist where email = 'third@co.com'").run();
  const fourth = await signup("fourth@co.com");
  assert.match(await fourth.text(), /#4 on the waitlist\./);
});

const BANNED_LEXICON =
  /!|\bwelcome\b|\boops\b|\bjust\b|\bsimply\b|\bawesome\b|\bboard(ing)?\b|\bpassengers?\b|\bshortly\b|\bsoon\b|\brecently\b|you'?re all set/i;

// The product is named ufo; the copy never plays the part.
const BANNED_METAPHOR =
  /\bbeam\w*|\btransmit\w*|\bsignals?\b|\bsaucers?\b|\bmothership\b|\bcraft\b|\bfleets?\b|\babduct\w*|\b(un)?identified\b|\bidentification\b|\bobjects?\b/i;

// Standard typography: a sentence never opens lowercase unless it opens with a literal —
// a command, an address, a header name. Lines split into sentences on a spaced terminator, and
// glyph-only tokens (✓, ›, art) are skipped so the word behind them is inspected. A terminator
// fused to its next word (`Done.try`, `Sent!check`) goes unjudged: the dot form is the same
// shape as a cased dotted literal (`Node.js`, `README.md`) and `?` rides in URLs, so with two
// of the three terminators ambiguous, fused terminators are uniformly out of scope — the
// accept list pins all three fused shapes as accepted.
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
    "#1 on the waitlist. We will email you when access opens.",
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
  const usage = await (await request("https://flyingobject.ai/waitlist")).text();
  const ack = await (
    await request("https://flyingobject.ai/waitlist", {
      method: "POST",
      body: "email=lexicon@co.com",
    })
  ).text();
  const sent = [];
  const isolated = {
    DB: d1(),
    ORIGIN_BASE: "https://testing.flyingobject.ai",
    WAITLIST_DEAD_LETTER_QUEUE: "ufo-edge-waitlist-email-dead-letters",
    WAITLIST_EMAILS: { async send() {} },
    EMAIL: {
      async send(message) {
        sent.push(message);
      },
    },
  };
  const fresh = await importWorker("waitlist-lexicon");
  await fresh.fetch(
    new Request("https://flyingobject.ai/waitlist", {
      method: "POST",
      body: "email=lexicon@co.com",
      headers: { "user-agent": "curl/8.6.0" },
    }),
    isolated,
  );
  await fresh.queue(
    {
      queue: "ufo-edge-waitlist-email",
      messages: [{ body: { email: "lexicon@co.com" }, ack() {} }],
    },
    isolated,
  );
  assert.equal(sent.length, 1);
  for (const surface of [card, usage, ack, sent[0].subject, sent[0].text]) {
    assert.doesNotMatch(surface, BANNED_LEXICON);
    assert.doesNotMatch(surface, BANNED_METAPHOR);
    assertStandardCase(surface);
  }
});
