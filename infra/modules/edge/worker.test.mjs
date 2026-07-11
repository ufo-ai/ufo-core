import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

// The same substitution main.tf applies at deploy, so the tested worker is the shipped artifact.
const moduleDir = new URL(".", import.meta.url);
const LANDING_PAGE = await readFile(new URL("landing.html", moduleDir), "utf8");
const source = (await readFile(new URL("worker.js", moduleDir), "utf8"))
  .replace('"__LANDING_HTML__"', JSON.stringify(LANDING_PAGE))
  .replace('"__WAITLIST_SENDER__"', JSON.stringify("no-reply@flyingobject.ai"));
async function importWorker(tag) {
  const tagged = `${source}\n// ${tag}`;
  return (await import(`data:text/javascript;base64,${Buffer.from(tagged).toString("base64")}`))
    .default;
}
const worker = await importWorker("shared");

function fakeD1() {
  const rows = new Map();
  return {
    prepare(sql) {
      const stmt = { sql, args: [] };
      return {
        bind(...args) {
          stmt.args = args;
          return this;
        },
        async run() {
          let changes = 0;
          if (stmt.sql.startsWith("insert") && !rows.has(stmt.args[0])) {
            rows.set(stmt.args[0], rows.size + 1);
            changes = 1;
          }
          return { meta: { changes } };
        },
        async first() {
          if (stmt.sql.includes("where created_at <=")) return { n: rows.get(stmt.args[0]) };
          return { n: rows.size };
        },
      };
    },
  };
}

const passedThrough = [];
let fleetReply = () => Response.json({ craft: 0 });
globalThis.fetch = async (input) => {
  const url = input instanceof Request ? input.url : input;
  if (url.endsWith("/fleet")) return fleetReply();
  passedThrough.push(url);
  return new Response(`origin:${url}`);
};

const env = {
  DB: fakeD1(),
  ORIGIN_BASE: "https://testing.flyingobject.ai",
  WAITLIST_EMAILS: { async send() {} },
  WAITLIST_DEAD_LETTER_QUEUE: "ufo-edge-waitlist-email-dead-letters",
};

function request(url, { ua = "curl/8.6.0", method = "GET", body } = {}) {
  return worker.fetch(new Request(url, { method, body, headers: { "user-agent": ua } }), env);
}

test("curl landing renders the card with the live count and https commands", async () => {
  const reply = await request("https://flyingobject.ai/");
  const body = await reply.text();
  assert.equal(reply.headers.get("content-type"), "text/plain; charset=utf-8");
  assert.match(body, /◉ ◉ ◉/);
  assert.match(body, /0 crafts on waitlist\./);
  assert.match(body, /curl https:\/\/flyingobject\.ai\/waitlist -d email=/);
  assert.match(body, /curl -fsSL https:\/\/flyingobject\.ai\/install \| sh/);
});

test("curl landing over plain http gets the card directly", async () => {
  const reply = await request("http://flyingobject.ai/");
  assert.equal(reply.status, 200);
  assert.equal(reply.headers.get("content-type"), "text/plain; charset=utf-8");
  assert.match(await reply.text(), /crafts on waitlist/);
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

test("joining is positional, idempotent, and normalizes the email", async () => {
  const first = await request("https://flyingobject.ai/waitlist", {
    method: "POST",
    body: "email=You@YourCo.com",
  });
  assert.equal(first.status, 200);
  assert.match(await first.text(), /transmission received: you@yourco\.com[\s\S]*#1\./);
  const second = await request("https://flyingobject.ai/waitlist", {
    method: "POST",
    body: "email=second@co.com&junk=1",
  });
  assert.match(await second.text(), /#2\./);
  const duplicate = await request("https://flyingobject.ai/waitlist", {
    method: "POST",
    body: "email=you@yourco.com",
  });
  assert.match(await duplicate.text(), /#1\./);
});

test("a first join queues and delivers one confirmation email", async () => {
  const queued = [];
  const sent = [];
  const isolated = {
    DB: fakeD1(),
    ORIGIN_BASE: "https://testing.flyingobject.ai",
    WAITLIST_DEAD_LETTER_QUEUE: "ufo-edge-waitlist-email-dead-letters",
    WAITLIST_EMAILS: {
      async send(message) {
        queued.push(message);
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
  assert.deepEqual(queued, [{ email: "pilot@example.com", position: 1 }]);

  let acknowledged = false;
  await fresh.queue(
    {
      queue: "ufo-edge-waitlist-email",
      messages: [
        {
          body: queued[0],
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
      subject: "You're on the flyingobject.ai waitlist",
      text:
        "Transmission received: pilot@example.com\n\n" +
        "You are flying object #1.\n" +
        "We'll signal you when it's time to board.\n",
    },
  ]);
  assert.equal(acknowledged, true);
});

test("a failed confirmation remains unacknowledged for queue retry", async () => {
  let acknowledged = false;
  await assert.rejects(
    worker.queue(
      {
        queue: "ufo-edge-waitlist-email",
        messages: [
          {
            body: { email: "pilot@example.com", position: 1 },
            ack() {
              acknowledged = true;
            },
          },
        ],
      },
      {
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

test("an exhausted confirmation is surfaced and consumed", async (context) => {
  const logged = context.mock.method(console, "error", () => {});
  let acknowledged = false;
  await worker.queue(
    {
      queue: "ufo-edge-waitlist-email-dead-letters",
      messages: [
        {
          body: { email: "pilot@example.com", position: 1 },
          ack() {
            acknowledged = true;
          },
        },
      ],
    },
    { WAITLIST_DEAD_LETTER_QUEUE: "ufo-edge-waitlist-email-dead-letters" },
  );
  assert.equal(acknowledged, true);
  assert.deepEqual(logged.mock.calls[0].arguments, [
    "waitlist confirmation failed for pilot@example.com",
  ]);
});

test("a join busts the counter cache so the card reflects it", async () => {
  const body = await (await request("https://flyingobject.ai/")).text();
  assert.match(body, /2 crafts on waitlist\./);
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
  assert.match(await next.text(), /#3\./);
});

test("GET /waitlist answers with usage for the requested host", async () => {
  const reply = await request("https://testing.flyingobject.ai/waitlist");
  assert.match(await reply.text(), /curl https:\/\/testing\.flyingobject\.ai\/waitlist/);
});

test("/install and /install.sh proxy the gateway's stamped client script", async () => {
  for (const path of ["/install", "/install.sh"]) {
    const reply = await request(`https://flyingobject.ai${path}`);
    assert.equal(await reply.text(), "origin:https://testing.flyingobject.ai/ufo");
  }
});

test("any other path passes through untouched", async () => {
  const reply = await request("https://flyingobject.ai/v1/onboard/ufo", { ua: "Mozilla/5.0" });
  assert.equal(await reply.text(), "origin:https://flyingobject.ai/v1/onboard/ufo");
});
