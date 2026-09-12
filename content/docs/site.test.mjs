import assert from "node:assert/strict";
import { readFile, readdir, stat } from "node:fs/promises";
import test from "node:test";

const dist = new URL("dist/", import.meta.url);
const brand = new URL("../../assets/brand/", import.meta.url);
const APEX = "https://ufo.ai";
const NOT_FOUND = "/404";
const EDGE_PATHS = new Set(["/", "/favicon.svg", "/favicon-dark.svg"]);
const CONNECTORS = new Map([
  ["/docs/connectors/datadog/", ["Connect Datadog for this workspace.", "Datadog"]],
  ["/docs/connectors/github/", ["Connect my GitHub account.", "GitHub"]],
  ["/docs/connectors/gmail/", ["Connect my Gmail account.", "Gmail"]],
  ["/docs/connectors/google-calendar/", ["Connect my Google Calendar account.", "Google Calendar"]],
  ["/docs/connectors/google-drive/", ["Connect my Google Drive account.", "Google Drive"]],
  ["/docs/connectors/google-sheets/", ["Connect my Google Sheets account.", "Google Sheets"]],
  ["/docs/connectors/hubspot/", ["Connect my HubSpot account.", "HubSpot"]],
  ["/docs/connectors/linear/", ["Connect my Linear account.", "Linear"]],
  [
    "/docs/connectors/mcp/",
    ["Connect an MCP server named docs at https://mcp.example.com/mcp.", "MCP"],
  ],
  ["/docs/connectors/notion/", ["Connect my Notion account.", "Notion"]],
  ["/docs/connectors/quickbooks/", ["Connect my QuickBooks account.", "QuickBooks"]],
  ["/docs/connectors/sentry/", ["Connect my Sentry account.", "Sentry"]],
  ["/docs/connectors/slack/", ["Connect this workspace to Slack.", "Slack"]],
  ["/docs/connectors/stripe/", ["Connect my Stripe account.", "Stripe"]],
  ["/docs/connectors/zendesk/", ["Connect my Zendesk account.", "Zendesk"]],
]);

async function pages(directory = dist, prefix = "/") {
  const found = new Map();
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (entry.name === "pagefind" || entry.name === "_astro") continue;
    if (entry.isDirectory()) {
      for (const [route, html] of await pages(
        new URL(`${entry.name}/`, directory),
        `${prefix}${entry.name}/`,
      )) {
        found.set(route, html);
      }
    } else if (entry.name === "index.html") {
      found.set(prefix, await readFile(new URL(entry.name, directory), "utf8"));
    } else if (entry.name.endsWith(".html")) {
      found.set(`${prefix}${entry.name.slice(0, -".html".length)}`, await readFile(new URL(entry.name, directory), "utf8"));
    }
  }
  return found;
}

const built = await pages();
assert.ok(
  built.size > 1,
  "dist holds no pages — run `pnpm -C content/docs run build` before this suite",
);

function links(html) {
  return [...html.matchAll(/href="(\/[^"#?]*)(?:[#?][^"]*)?"/g)].map(([, path]) => path);
}

async function served(path) {
  if (EDGE_PATHS.has(path)) return true;
  if (built.has(path)) return true;
  if (!path.endsWith("/") && built.has(`${path}/`)) return true;
  try {
    return (await stat(new URL(`.${path}`, dist))).isFile();
  } catch {
    return false;
  }
}

test("every internal link the site draws is a page or a file it built", async () => {
  const broken = [];
  for (const [route, html] of built) {
    for (const path of links(html)) {
      if (!(await served(path))) broken.push(`${route} -> ${path}`);
    }
  }
  assert.deepEqual(broken, []);
});

test("every page is reached from another page", () => {
  const reached = new Set();
  for (const [route, html] of built) {
    for (const path of links(html)) {
      if (path !== route) reached.add(path.endsWith("/") ? path : `${path}/`);
    }
  }
  const orphans = [...built.keys()].filter(
    (route) => route !== "/" && route !== NOT_FOUND && !reached.has(route),
  );
  assert.deepEqual(orphans, []);
});

test("every connector page offers chat or the filtered Connectors page", () => {
  const routes = [...built.keys()].filter(
    (route) => route.startsWith("/docs/connectors/") && route !== "/docs/connectors/",
  );
  assert.deepEqual(routes.sort(), [...CONNECTORS.keys()].sort());
  for (const [route, [prompt, query]] of CONNECTORS) {
    const html = built.get(route);
    assert.ok(html.includes(`data-connect-prompt="${prompt}"`), `${route} has no chat prompt`);
    const path = `/surface/web#/connectors?q=${encodeURIComponent(query)}`;
    assert.ok(html.includes(`data-connector-path="${path}"`), `${route} has the wrong filter`);
    assert.ok(html.includes(`href="https://app.ufo.ai${path}"`), `${route} has no production link`);
    assert.ok(
      html.includes('window.location.hostname===`ufo.ai`?`app.ufo.ai`:`app.testing.ufo.ai`'),
      `${route} sends production to testing`,
    );
  }
});

test("every canonical names production, so the testing door is never the indexed copy", () => {
  for (const [route, html] of built) {
    const canonical = html.match(/<link rel="canonical" href="([^"]+)"/);
    assert.ok(canonical, `${route} declares no canonical`);
    assert.ok(
      canonical[1].startsWith(APEX),
      `${route} points its canonical at ${canonical[1]}`,
    );
  }
});

test("documentation and blog pages stay under their paths", () => {
  assert.ok(built.has("/docs/"));
  assert.ok(built.has("/blog/"));
  assert.deepEqual(
    [...built.keys()].filter(
      (route) =>
        route !== NOT_FOUND && !route.startsWith("/docs/") && !route.startsWith("/blog/"),
    ),
    [],
  );
});

test("the blog advertises its production feed", async () => {
  assert.match(
    built.get("/blog/"),
    /<link href="https:\/\/ufo\.ai\/blog\/rss\.xml" rel="alternate" title="Blog" type="application\/rss\+xml"\/>/,
  );
  const feed = await readFile(new URL("blog/rss.xml", dist), "utf8");
  assert.match(feed, /<title>UFO \| Blog<\/title>/);
  assert.match(feed, /<atom:link rel="self" href="https:\/\/ufo\.ai\/blog\/rss\.xml"\/>/);
});

test("the header draws the lockup, one file per scheme", () => {
  const header = built.get("/docs/").match(/<a[^>]*class="site-title[^"]*"[^>]*>(.*?)<\/a>/s);
  assert.ok(header, "no site title in the header");
  const drawn = [...header[1].matchAll(/src="([^"]+)"/g)].map(([, src]) => src);
  assert.equal(drawn.length, 2, `the header draws ${drawn.length} images, not one per scheme`);
  for (const src of drawn) assert.match(src, /lockup(-on-dark)?\.[A-Za-z0-9_-]+\.svg$/);
});

test("the mark is served from its one home", async () => {
  for (const [served_as, source] of [
    ["favicon.svg", "ufo-mark.svg"],
    ["favicon-dark.svg", "ufo-mark-on-dark.svg"],
  ]) {
    assert.equal(
      await readFile(new URL(served_as, dist), "utf8"),
      await readFile(new URL(source, brand), "utf8"),
    );
  }
});
