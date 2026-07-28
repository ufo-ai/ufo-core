import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";

import { LANDING_PAGE, d1, importWorker } from "./harness.mjs";

const worker = await importWorker("shared");

const EMAIL_LEDGER =
  "create table if not exists waitlist_email (email text primary key, queued_at text, sent_at text)";

const passedThrough = [];
let fleetReply = () => Response.json({ craft: 0 });
globalThis.fetch = async (input) => {
  const url = input instanceof Request ? input.url : input;
  if (url.endsWith("/fleet")) return fleetReply();
  passedThrough.push(url);
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
  assert.match(body, /join the waitlist:/);
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
    /^<!doctype html>\n<!-- terminal interface: curl https:\/\/flyingobject\.ai -->/,
  );
  assert.match(
    LANDING_PAGE,
    /<meta name="description" content="[^"]*curl https:\/\/flyingobject\.ai" \/>/,
  );
  assert.match(
    LANDING_PAGE,
    /<span hidden aria-hidden="true">terminal interface: curl https:\/\/flyingobject\.ai<\/span>/,
  );
  assert.doesNotMatch(LANDING_PAGE, /terminal-hint/);
  assert.match(
    LANDING_PAGE,
    /console\.info\("terminal interface: curl https:\/\/flyingobject\.ai"\);/,
  );
});

test("every front door serves its own embedded page to browsers", async () => {
  for (const host of ["flyingobject.ai", "testing.flyingobject.ai"]) {
    const reply = await request(`https://${host}/?utm_source=card`, { ua: "Mozilla/5.0" });
    assert.equal(reply.headers.get("content-type"), "text/html; charset=utf-8");
    assert.equal(await reply.text(), LANDING_PAGE.replace("__FLEET_N__", "0"));
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
  assert.match(await first.text(), /#1 on the waitlist\. we will email you when access opens\./);
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
  const [, action] = LANDING_PAGE.match(/<form id="join" action="([^"]+)" method="post">/);
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

test("a craft's click survives the morph that is redrawing it", () => {
  // The animation replaces every glyph each frame: a press landing on one destroys its own
  // target before the release, so the click never resolves. Only the craft is hit-tested, and
  // the handler sits on the sky they share — the fleet gathers, so press and release can land
  // on two different craft. Without both, clicking a craft works about a quarter of the time.
  assert.match(LANDING_PAGE, /\.craft span\{pointer-events:none\}/);
  assert.match(LANDING_PAGE, /sky\.addEventListener\('click', hail\);/);
});

test("the sky is decoration and the join is reachable without a pointer", () => {
  assert.match(LANDING_PAGE, /<div id="sky" aria-hidden="true"><\/div>/);
  assert.match(LANDING_PAGE, /<button id="hail" class="hail" type="button">/);
  assert.match(LANDING_PAGE, /<input id="email" name="email" type="email" required maxlength="254"/);
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

test("/ufo proxies the gateway's stamped client script", async () => {
  const reply = await request("https://flyingobject.ai/ufo");
  assert.equal(await reply.text(), "origin:https://testing.flyingobject.ai/ufo");
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
  }
});
