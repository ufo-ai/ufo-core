import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";

import { LANDING_PAGE, d1, importWorker, landingPage } from "./harness.mjs";

const worker = await importWorker("shared");

const EMAIL_LEDGER =
  "create table if not exists waitlist_email (email text primary key, queued_at text, sent_at text)";

const outbound = [];
let fleetReply = () => Response.json({ craft: 0 });
globalThis.fetch = async (input) => {
  const url = input instanceof Request ? input.url : input;
  outbound.push(url);
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
  assert.match(body, /0 workspaces\. 0 on the waitlist\./);
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

test("browser landing hides the terminal hint from sight", () => {
  assert.match(
    LANDING_PAGE,
    /^<!doctype html>\n<!-- Terminal interface: curl https:\/\/flyingobject\.ai -->/,
  );
  assert.match(
    LANDING_PAGE,
    /<meta name="description" content="[^"]*curl https:\/\/flyingobject\.ai" \/>/,
  );
  assert.match(
    LANDING_PAGE,
    /<span hidden aria-hidden="true">Terminal interface: curl https:\/\/flyingobject\.ai<\/span>/,
  );
  assert.doesNotMatch(LANDING_PAGE, /terminal-hint/);
  assert.match(
    LANDING_PAGE,
    /console\.info\("Terminal interface: curl https:\/\/flyingobject\.ai"\);/,
  );
});

test("every front door serves its own embedded page to browsers", async () => {
  for (const host of ["flyingobject.ai", "testing.flyingobject.ai"]) {
    const fresh = await importWorker(`page-${host}`, host);
    const reply = await fresh.fetch(
      new Request(`https://${host}/?utm_source=card`, {
        headers: { "user-agent": "Mozilla/5.0" },
      }),
      { ...env, ORIGIN_BASE: `https://origin.${host}` },
    );
    const page = await reply.text();
    assert.equal(reply.headers.get("content-type"), "text/html; charset=utf-8");
    assert.equal(page, landingPage(host).replace("__FLEET_N__", "0"));
  }
});

test("the landing page carries the live craft count from the gateway", async () => {
  const fresh = await importWorker("fleet-live");
  fleetReply = () => Response.json({ craft: 4 });
  const reply = await fresh.fetch(
    new Request("https://flyingobject.ai/", { headers: { "user-agent": "Mozilla/5.0" } }),
    env,
  );
  assert.match(await reply.text(), /const FLEET_N = 4;/);
});

test("the craft count is capped at the fleet limit", async () => {
  const fresh = await importWorker("fleet-cap");
  fleetReply = () => Response.json({ craft: 5000 });
  const reply = await fresh.fetch(
    new Request("https://flyingobject.ai/", { headers: { "user-agent": "Mozilla/5.0" } }),
    env,
  );
  assert.match(await reply.text(), /const FLEET_N = 100;/);
});

test("a gateway outage lands an empty sky, not an error", async () => {
  const fresh = await importWorker("fleet-outage");
  fleetReply = () => {
    throw new Error("origin down");
  };
  const reply = await fresh.fetch(
    new Request("https://flyingobject.ai/", { headers: { "user-agent": "Mozilla/5.0" } }),
    env,
  );
  assert.equal(reply.status, 200);
  assert.match(await reply.text(), /const FLEET_N = 0;/);
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

test("the browser panel joins over the same wire the card documents", async () => {
  // Drive the worker with exactly what the page declares and sends, so a change to either
  // end of the panel's request has to keep landing on a route the worker actually serves.
  const [, action] = LANDING_PAGE.match(/<form id="join"[^>]* action="([^"]+)"/);
  const [, contentType] = LANDING_PAGE.match(/'content-type': '([^']+)'/);
  const fresh = await importWorker("panel-join");
  const reply = await fresh.fetch(
    new Request(new URL(action, "https://flyingobject.ai"), {
      method: "POST",
      headers: { "content-type": contentType, "user-agent": "Mozilla/5.0" },
      body: new URLSearchParams({ email: "Pilot@YourCo.com" }),
    }),
    { ...env, DB: d1() },
  );
  assert.equal(reply.status, 200);
  assert.match((await reply.text()).trim(), /^#1 on the waitlist\./);
});

test("the sky is decoration the pointer passes straight through", () => {
  assert.match(LANDING_PAGE, /<div id="sky" aria-hidden="true"><\/div>/);
  assert.match(LANDING_PAGE, /\.craft\{[^}]*pointer-events:none/);
  assert.doesNotMatch(LANDING_PAGE, /addEventListener\('click'/);
});

test("the join is a block in the page, submittable with nothing to open", () => {
  assert.doesNotMatch(LANDING_PAGE, /dialog|showModal|aria-modal|::backdrop/i);
  assert.match(
    LANDING_PAGE,
    /<form id="join" class="panel" action="\/waitlist" method="post" aria-labelledby="join-head">/,
  );
  assert.match(LANDING_PAGE, /<p class="head" id="join-head">Join Waitlist<\/p>/);
  assert.match(LANDING_PAGE, /<input id="email" name="email" type="email" required maxlength="254"/);
  assert.match(LANDING_PAGE, /<button id="go" class="go" type="submit">join<\/button>/);
  assert.match(LANDING_PAGE, /<p id="ack" class="ack" role="status"><\/p>/);
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
  assert.match(body, /0 workspaces\. 2 on the waitlist\./);
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
    const fleet = await fresh.fetch(new Request(`https://${host}/fleet`), doorEnv);
    assert.equal(await installer.text(), `origin:${origin}/ufo`);
    assert.deepEqual(await fleet.json(), { craft: 0 });
    assert.deepEqual(outbound.slice(start), [
      `${origin}/fleet`,
      `${origin}/ufo`,
      `${origin}/fleet`,
    ]);
  }
});

test("any other path passes through untouched", async () => {
  const reply = await request("https://flyingobject.ai/v1/onboard/ufo", { ua: "Mozilla/5.0" });
  assert.equal(await reply.text(), "origin:https://flyingobject.ai/v1/onboard/ufo");
});

test("apex /login 302s to the app host, the sole authenticated origin", async () => {
  const prod = await request("https://flyingobject.ai/login", { ua: "Mozilla/5.0" });
  assert.equal(prod.status, 302);
  assert.equal(prod.headers.get("location"), "https://app.flyingobject.ai/login");
  const testing = await request("https://testing.flyingobject.ai/login", { ua: "Mozilla/5.0" });
  assert.equal(testing.headers.get("location"), "https://app.testing.flyingobject.ai/login");
});

test("/ufo serves byte-identical content to every user agent", async () => {
  const cli = await (await request("https://flyingobject.ai/ufo")).text();
  const browser = await (
    await request("https://flyingobject.ai/ufo", { ua: "Mozilla/5.0" })
  ).text();
  assert.equal(cli, browser);
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
