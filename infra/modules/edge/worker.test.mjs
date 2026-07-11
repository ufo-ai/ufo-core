import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

// The same substitution main.tf applies at deploy, so the tested worker is the shipped artifact.
const moduleDir = new URL(".", import.meta.url);
const LANDING_PAGE = await readFile(new URL("landing.html", moduleDir), "utf8");
const source = (await readFile(new URL("worker.js", moduleDir), "utf8")).replace(
  '"__LANDING_HTML__"',
  JSON.stringify(LANDING_PAGE),
);
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
          if (stmt.sql.startsWith("insert") && !rows.has(stmt.args[0])) {
            rows.set(stmt.args[0], rows.size + 1);
          }
          return {};
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
