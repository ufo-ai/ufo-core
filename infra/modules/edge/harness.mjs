import { readFile } from "node:fs/promises";

const moduleDir = new URL(".", import.meta.url);
export const FAVICON_SVG = await readFile(
  new URL("../../../assets/brand/ufo-mark.svg", moduleDir),
  "utf8",
);
export const FAVICON_DARK_SVG = await readFile(
  new URL("../../../assets/brand/ufo-mark-on-dark.svg", moduleDir),
  "utf8",
);
const sourceTemplate = await readFile(new URL("worker.js", moduleDir), "utf8");
export const LANDING_PAGE = await readFile(new URL("landing.html", moduleDir), "utf8");

const legalShell = await readFile(new URL("legal.html", moduleDir), "utf8");
const legalPage = async ({ title, description, canonical, body }) =>
  legalShell
    .replaceAll("__TITLE__", title)
    .replaceAll("__DESCRIPTION__", description)
    .replaceAll("__CANONICAL__", canonical)
    .replace("__BODY__", await readFile(new URL(body, moduleDir), "utf8"));
export const PRIVACY_DESCRIPTION =
  "What ufo.ai collects when you sign in and use the service, how that information is used, and how long it is kept.";
export const TERMS_DESCRIPTION =
  "The terms that govern your use of ufo.ai: accounts, acceptable use, intellectual property, and liability.";
export const PRIVACY_PAGE = await legalPage({
  title: "Privacy Policy",
  description: PRIVACY_DESCRIPTION,
  canonical: "https://ufo.ai/privacy",
  body: "privacy.html",
});
export const TERMS_PAGE = await legalPage({
  title: "Terms of Service",
  description: TERMS_DESCRIPTION,
  canonical: "https://ufo.ai/terms",
  body: "terms.html",
});

export async function importWorker(tag) {
  const source = sourceTemplate
    .replace('"__LANDING_HTML__"', JSON.stringify(LANDING_PAGE))
    .replace('"__FAVICON_SVG__"', JSON.stringify(FAVICON_SVG))
    .replace('"__FAVICON_DARK_SVG__"', JSON.stringify(FAVICON_DARK_SVG))
    .replace('"__PRIVACY_HTML__"', JSON.stringify(PRIVACY_PAGE))
    .replace('"__TERMS_HTML__"', JSON.stringify(TERMS_PAGE));
  const tagged = `${source}\n// ${tag}`;
  return (await import(`data:text/javascript;base64,${Buffer.from(tagged).toString("base64")}`))
    .default;
}

export function edgeCache() {
  const stored = new Map();
  return {
    stored,
    default: {
      async match(request) {
        const entry = stored.get(request.url);
        if (entry === undefined) return undefined;
        return new Response(entry.body, { status: entry.status, headers: entry.headers });
      },
      async put(request, response) {
        stored.set(request.url, {
          body: await response.arrayBuffer(),
          status: response.status,
          headers: [...response.headers],
        });
      },
    },
  };
}

