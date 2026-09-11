const CLI_UA = /^(curl|wget|httpie)\b/i;
const PAGE_CACHE = "public, max-age=600";
const APEX = "https://ufo.ai";
const INDEXED = ["/", "/privacy", "/slack", "/support", "/terms"];
const UNCRAWLED = ["/ufo", "/fleet", "/v1/onboard/", "/login"];
const SITE_CARD_PREFIX = "/surface/sites/share/site/";
const ARTIFACT_PREFIX = "/artifacts/";
const JOIN_PREFIX = "/join/";
const JOIN_URL = `${APEX}${JOIN_PREFIX}ufo`;
const ARTIFACT_CACHE_MAX_BYTES = 24 * 1024 * 1024;

const LANDING_HTML = "__LANDING_HTML__";
const FAVICON_SVG = "__FAVICON_SVG__";
const FAVICON_DARK_SVG = "__FAVICON_DARK_SVG__";
const PRIVACY_HTML = "__PRIVACY_HTML__";
const SLACK_HTML = "__SLACK_HTML__";
const SUPPORT_HTML = "__SUPPORT_HTML__";
const TERMS_HTML = "__TERMS_HTML__";

function card(host) {
  return `
  ∵ ${host}
  Build the unknown.

  Sign up: ${JOIN_URL}

  Install: curl -fsSL https://${host}/ufo | sh

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

function page(body, type = "text/html; charset=utf-8") {
  return new Response(body, {
    headers: { "cache-control": PAGE_CACHE, "content-type": type },
  });
}

function sitemap() {
  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ...INDEXED.map((path) => `  <url><loc>${APEX}${path}</loc></url>`),
    "</urlset>",
    "",
  ].join("\n");
}

function robots() {
  return [
    "User-agent: *",
    "Allow: /",
    ...UNCRAWLED.map((path) => `Disallow: ${path}`),
    "",
    `Sitemap: ${APEX}/sitemap.xml`,
    "",
  ].join("\n");
}

async function siteCard(url) {
  const key = new Request(`${url.origin}${url.pathname}`);
  const cached = await caches.default.match(key);
  if (cached) return cached;
  const served = await fetch(key);
  if (served.ok) await caches.default.put(key, served.clone());
  return served;
}

async function artifactBytes(request, url) {
  const key = new Request(`${url.origin}${url.pathname}${url.search}`);
  const cached = await caches.default.match(key);
  if (cached) return cached;
  const served = await fetch(request);
  const directive = served.headers.get("cache-control") ?? "";
  const size = Number(served.headers.get("content-length"));
  const bounded = Number.isFinite(size) && size > 0 && size <= ARTIFACT_CACHE_MAX_BYTES;
  if (served.status === 200 && directive.includes("public") && bounded) {
    await caches.default.put(key, served.clone());
  }
  return served;
}

function landing(request, url) {
  if (!CLI_UA.test(request.headers.get("user-agent") ?? "")) {
    const bounce = secure(url);
    if (bounce) return bounce;
    return page(LANDING_HTML);
  }
  return text(card(url.hostname));
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
    if (request.method === "GET" && url.pathname.startsWith(JOIN_PREFIX)) {
      return Response.redirect(`https://app.${url.hostname}${url.pathname}`, 302);
    }
    if (request.method === "GET" && url.pathname.startsWith(SITE_CARD_PREFIX)) {
      return siteCard(url);
    }
    if (request.method === "GET" && url.pathname.startsWith(ARTIFACT_PREFIX)) {
      return artifactBytes(request, url);
    }
    switch (url.pathname) {
      case "/":
        return landing(request, url);
      case "/favicon.svg":
        return new Response(FAVICON_SVG, {
          headers: { "cache-control": "no-cache", "content-type": "image/svg+xml" },
        });
      case "/favicon-dark.svg":
        return new Response(FAVICON_DARK_SVG, {
          headers: { "cache-control": "no-cache", "content-type": "image/svg+xml" },
        });
      case "/robots.txt":
        return secure(url) ?? page(robots(), "text/plain; charset=utf-8");
      case "/sitemap.xml":
        return secure(url) ?? page(sitemap(), "application/xml; charset=utf-8");
      case "/privacy":
        return secure(url) ?? page(PRIVACY_HTML);
      case "/slack":
        return secure(url) ?? page(SLACK_HTML);
      case "/support":
        return secure(url) ?? page(SUPPORT_HTML);
      case "/terms":
        return secure(url) ?? page(TERMS_HTML);
      case "/ufo":
        return fetch(`${env.ORIGIN_BASE}/ufo`);
      case "/fleet":
        return fetch(`${env.ORIGIN_BASE}/fleet`);
      case "/login":
        return Response.redirect(`https://app.${url.hostname}/login${url.search}`, 302);
      case "/logout":
        return Response.redirect(`https://app.${url.hostname}/logout${url.search}`, 302);
      default:
        return fetch(request);
    }
  },
};
