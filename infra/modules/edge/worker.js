const CLI_UA = /^(curl|wget|httpie)\b/i;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MAX_EMAIL = 254;
const COUNT_TTL_MS = 3_600_000;
const FLEET_TTL_MS = 300_000;
const FLEET_FETCH_TIMEOUT_MS = 3_000;
const MAX_FLEET = 100;
const WAITLIST_SENDER = "__WAITLIST_SENDER__";

const LANDING_HTML = "__LANDING_HTML__";
const FAVICON_SVG = "__FAVICON_SVG__";
const FAVICON_DARK_SVG = "__FAVICON_DARK_SVG__";
const PRIVACY_HTML = "__PRIVACY_HTML__";
const TERMS_HTML = "__TERMS_HTML__";

const SCHEMA =
  "create table if not exists waitlist (" +
  "  n integer primary key autoincrement," +
  "  email text not null unique," +
  "  created_at text not null default (datetime('now')))";
const NUMBERED =
  "select count(*) as numbered from sqlite_master" +
  " where name = 'waitlist' and sql like '%autoincrement%'";
const RENUMBER = [
  "create table waitlist_numbered (" +
  "  n integer primary key autoincrement," +
  "  email text not null unique," +
  "  created_at text not null default (datetime('now')))",
  "insert into waitlist_numbered (email, created_at)" +
  " select email, created_at from waitlist order by rowid",
  "drop table waitlist",
  "alter table waitlist_numbered rename to waitlist",
];
const EMAIL_SCHEMA =
  "create table if not exists waitlist_email (" +
  "  email text primary key," +
  "  queued_at text," +
  "  sent_at text)";

let counted = { count: null, at: 0 };
let fleet = { count: null, at: 0 };

function card(host, total, workspaces) {
  return `
      ╭─◠◠◠─╮
  ╾══╡ ◉ ◉ ◉ ╞══╼   ${host}
     ╰┄┄┄┄┄┄┄╯

  ${workspaces} workspace${workspaces === 1 ? "" : "s"}. ${total} on the waitlist.

  Join the waitlist:
    curl https://${host}/waitlist -d email=email@work.com

  Already invited?
    curl -fsSL https://${host}/ufo | sh

`;
}

function usage(host) {
  return `
  Join the waitlist:
    curl https://${host}/waitlist -d email=email@work.com

`;
}

function ack(position) {
  return `
  #${position} on the waitlist. We will email you when access opens.

`;
}

function text(body, status = 200) {
  return new Response(body, {
    status,
    headers: { "content-type": "text/plain; charset=utf-8" },
  });
}

function secure(url) {
  if (url.protocol !== "http:") return null;
  const https = new URL(url);
  https.protocol = "https:";
  return Response.redirect(https.href, 301);
}

function page(html) {
  return new Response(html, {
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}

async function ensureWaitlist(db) {
  await db.prepare(SCHEMA).run();
  const { numbered } = await db.prepare(NUMBERED).first();
  if (numbered === 0) {
    await db.batch(RENUMBER.map((statement) => db.prepare(statement)));
  }
}

async function waitlistCount(db) {
  if (counted.count === null || Date.now() - counted.at > COUNT_TTL_MS) {
    await ensureWaitlist(db);
    const row = await db.prepare("select count(*) as n from waitlist").first();
    counted = { count: row.n, at: Date.now() };
  }
  return counted.count;
}

async function fleetCount(originBase) {
  if (fleet.count !== null && Date.now() - fleet.at <= FLEET_TTL_MS) {
    return fleet.count;
  }
  try {
    const reply = await fetch(`${originBase}/fleet`, {
      signal: AbortSignal.timeout(FLEET_FETCH_TIMEOUT_MS),
    });
    const { craft } = await reply.json();
    fleet = { count: Math.max(4, Number(craft) || 0), at: Date.now() };
  } catch {
    return fleet.count ?? 0;
  }
  return fleet.count;
}

async function landing(request, env, url) {
  if (!CLI_UA.test(request.headers.get("user-agent") ?? "")) {
    const bounce = secure(url);
    if (bounce) return bounce;
    const count = await fleetCount(env.ORIGIN_BASE);
    return page(LANDING_HTML.replace("__FLEET_N__", String(Math.min(MAX_FLEET, count))));
  }
  const [total, workspaces] = await Promise.all([
    waitlistCount(env.DB),
    fleetCount(env.ORIGIN_BASE),
  ]);
  return text(card(url.hostname, total, workspaces));
}

async function join(request, env, url) {
  const form = new URLSearchParams(await request.text());
  const email = (form.get("email") ?? "").trim().toLowerCase();
  if (!EMAIL.test(email) || email.length > MAX_EMAIL) {
    return text(usage(url.hostname), 400);
  }
  await ensureWaitlist(env.DB);
  await env.DB.prepare(
    "insert into waitlist (email)" +
    " select ?1 where not exists (select 1 from waitlist where email = ?1)",
  )
    .bind(email)
    .run();
  const row = await env.DB.prepare("select n from waitlist where email = ?1").bind(email).first();
  await env.DB.prepare(EMAIL_SCHEMA).run();
  await env.DB.prepare("insert into waitlist_email (email) values (?1) on conflict do nothing")
    .bind(email)
    .run();
  const delivery = await env.DB.prepare(
    "select queued_at, sent_at from waitlist_email where email = ?1",
  )
    .bind(email)
    .first();
  if (delivery.queued_at === null && delivery.sent_at === null) {
    await env.WAITLIST_EMAILS.send({ email }, { contentType: "json" });
    await env.DB.prepare("update waitlist_email set queued_at = datetime('now') where email = ?1")
      .bind(email)
      .run();
  }
  counted = { count: null, at: 0 };
  return text(ack(row.n));
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/v1/onboard/")) {
      return fetch(new Request(`${env.ORIGIN_BASE}${url.pathname}${url.search}`, request));
    }
    if (url.pathname.startsWith("/ufo/bin/")) {
      return fetch(`${env.ORIGIN_BASE}${url.pathname}`);
    }
    switch (url.pathname) {
      case "/":
        return landing(request, env, url);
      case "/favicon.svg":
        return new Response(FAVICON_SVG, {
          headers: { "cache-control": "no-cache", "content-type": "image/svg+xml" },
        });
      case "/favicon-dark.svg":
        return new Response(FAVICON_DARK_SVG, {
          headers: { "cache-control": "no-cache", "content-type": "image/svg+xml" },
        });
      case "/waitlist":
        return request.method === "POST" ? join(request, env, url) : text(usage(url.hostname));
      case "/privacy":
        return secure(url) ?? page(PRIVACY_HTML);
      case "/terms":
        return secure(url) ?? page(TERMS_HTML);
      case "/ufo":
        return fetch(`${env.ORIGIN_BASE}/ufo`);
      case "/fleet":
        return fetch(`${env.ORIGIN_BASE}/fleet`);
      case "/login":
        return Response.redirect(`https://app.${url.hostname}/login`, 302);
      default:
        return fetch(request);
    }
  },
  async queue(batch, env) {
    if (batch.queue === env.WAITLIST_DEAD_LETTER_QUEUE) {
      await Promise.all(
        batch.messages.map(async (message) => {
          await env.DB.prepare("update waitlist_email set queued_at = null where email = ?1")
            .bind(message.body.email)
            .run();
          console.error(`waitlist confirmation failed for ${message.body.email}`);
          message.ack();
        }),
      );
      return;
    }
    await ensureWaitlist(env.DB);
    await Promise.all(
      batch.messages.map(async (message) => {
        const { email } = message.body;
        const delivery = await env.DB.prepare(
          "select queued_at, sent_at from waitlist_email where email = ?1",
        )
          .bind(email)
          .first();
        if (delivery.sent_at !== null) {
          message.ack();
          return;
        }
        const entry = await env.DB.prepare("select n from waitlist where email = ?1")
          .bind(email)
          .first();
        await env.EMAIL.send({
          to: email,
          from: WAITLIST_SENDER,
          subject: `#${entry.n} on the ufo waitlist`,
          text: `${email} is #${entry.n} on the waitlist. We will email you when access opens.\n`,
        });
        await env.DB.prepare("update waitlist_email set sent_at = datetime('now') where email = ?1")
          .bind(email)
          .run();
        message.ack();
      }),
    );
  },
};
