const CLI_UA = /^(curl|wget|httpie)\b/i;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MAX_EMAIL = 254;
const COUNT_TTL_MS = 3_600_000;
const FLEET_TTL_MS = 300_000;
const MAX_FLEET = 100;

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
    const { craft } = await (await fetch(`${originBase}/fleet`)).json();
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
    if (url.hostname !== new URL(env.SITE_BASE).hostname) {
      return Response.redirect(`${env.SITE_BASE}/${url.search}`, 302);
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
  await env.DB.prepare("insert into waitlist (email) values (?1) on conflict (email) do nothing")
    .bind(email)
    .run();
  const row = await env.DB.prepare(
    "select count(*) as n from waitlist" +
    " where created_at <= (select created_at from waitlist where email = ?1)",
  )
    .bind(email)
    .first();
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
};
