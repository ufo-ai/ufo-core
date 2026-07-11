const CLI_UA = /^(curl|wget|httpie)\b/i;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MAX_EMAIL = 254;
const COUNT_TTL_MS = 3_600_000;
const FLEET_TTL_MS = 300_000;
const FLEET_FETCH_TIMEOUT_MS = 3_000;
const MAX_FLEET = 100;
const WAITLIST_SENDER = "__WAITLIST_SENDER__";
const WAITLIST_SUBJECT = "You're on the flyingobject.ai waitlist";

const LANDING_HTML = "__LANDING_HTML__";

const SCHEMA =
  "create table if not exists waitlist (" +
  "  email text primary key," +
  "  created_at text not null default (datetime('now')))";

let counted = { count: null, at: 0 };
let fleet = { count: null, at: 0 };

function card(host, count) {
  return `
       .  *   .      .

   .      ╭─◠◠◠─╮
      ╾══╡ ◉ ◉ ◉ ╞══╼      you found us.
         ╰┄┄┄┄┄┄┄╯         ${host}
           ˙ ✦ ˙
       .   *  .    .

  ${count} craft${count === 1 ? "" : "s"} on waitlist.

  join the waitlist:
    curl https://${host}/waitlist -d email=you@yourco.com

  have a code?
    curl -fsSL https://${host}/install | sh

`;
}

function usage(host) {
  return `
  join the waitlist:
    curl https://${host}/waitlist -d email=you@yourco.com

`;
}

function ack(email, position) {
  return `
  transmission received: ${email}

  you are flying object #${position}.
  we'll signal you when it's time to board.

`;
}

function text(body, status = 200) {
  return new Response(body, {
    status,
    headers: { "content-type": "text/plain; charset=utf-8" },
  });
}

async function waitlistCount(db) {
  if (counted.count === null || Date.now() - counted.at > COUNT_TTL_MS) {
    await db.prepare(SCHEMA).run();
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
    fleet = { count: Math.min(MAX_FLEET, Math.max(0, Number(craft) || 0)), at: Date.now() };
  } catch {
    return fleet.count ?? 0;
  }
  return fleet.count;
}

async function landing(request, env, url) {
  if (!CLI_UA.test(request.headers.get("user-agent") ?? "")) {
    if (url.protocol === "http:") {
      url.protocol = "https:";
      return Response.redirect(url.href, 301);
    }
    const count = await fleetCount(env.ORIGIN_BASE);
    return new Response(LANDING_HTML.replace("__FLEET_N__", String(count)), {
      headers: { "content-type": "text/html; charset=utf-8" },
    });
  }
  return text(card(url.hostname, await waitlistCount(env.DB)));
}

async function join(request, env, url) {
  const form = new URLSearchParams(await request.text());
  const email = (form.get("email") ?? "").trim().toLowerCase();
  if (!EMAIL.test(email) || email.length > MAX_EMAIL) {
    return text(usage(url.hostname), 400);
  }
  await env.DB.prepare(SCHEMA).run();
  const inserted = await env.DB.prepare(
    "insert into waitlist (email) values (?1) on conflict (email) do nothing",
  )
    .bind(email)
    .run();
  const row = await env.DB.prepare(
    "select count(*) as n from waitlist" +
    " where created_at <= (select created_at from waitlist where email = ?1)",
  )
    .bind(email)
    .first();
  if (inserted.meta.changes === 1) {
    await env.WAITLIST_EMAILS.send({ email, position: row.n });
  }
  counted = { count: null, at: 0 };
  return text(ack(email, row.n));
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    switch (url.pathname) {
      case "/":
        return landing(request, env, url);
      case "/waitlist":
        return request.method === "POST" ? join(request, env, url) : text(usage(url.hostname));
      case "/install":
      case "/install.sh":
        return fetch(`${env.ORIGIN_BASE}/ufo`);
      default:
        return fetch(request);
    }
  },
  async queue(batch, env) {
    if (batch.queue === env.WAITLIST_DEAD_LETTER_QUEUE) {
      batch.messages.forEach((message) => {
        console.error(`waitlist confirmation failed for ${message.body.email}`);
        message.ack();
      });
      return;
    }
    await Promise.all(
      batch.messages.map(async (message) => {
        const { email, position } = message.body;
        await env.EMAIL.send({
          to: email,
          from: WAITLIST_SENDER,
          subject: WAITLIST_SUBJECT,
          text:
            `Transmission received: ${email}\n\n` +
            `You are flying object #${position}.\n` +
            "We'll signal you when it's time to board.\n",
        });
        message.ack();
      }),
    );
  },
};
