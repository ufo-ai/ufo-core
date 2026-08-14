// The same substitution main.tf applies at deploy, so the tested worker is the shipped artifact.
import { readFile } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";

const moduleDir = new URL(".", import.meta.url);
const landingTemplate = await readFile(new URL("landing.html", moduleDir), "utf8");
export const FAVICON_SVG = await readFile(
  new URL("../../../assets/brand/ufo-mark.svg", moduleDir),
  "utf8",
);
export const FAVICON_DARK_SVG = await readFile(
  new URL("../../../assets/brand/ufo-mark-on-dark.svg", moduleDir),
  "utf8",
);
const sourceTemplate = await readFile(new URL("worker.js", moduleDir), "utf8");
export const landingPage = (hostname) => landingTemplate.replaceAll("__HOSTNAME__", hostname);
export const LANDING_PAGE = landingPage("flyingobject.ai");

const legalShell = await readFile(new URL("legal.html", moduleDir), "utf8");
const legalPage = async (title, body) =>
  legalShell
    .replaceAll("__TITLE__", title)
    .replace("__BODY__", await readFile(new URL(body, moduleDir), "utf8"));
export const PRIVACY_PAGE = await legalPage("Privacy Policy", "privacy.html");
export const TERMS_PAGE = await legalPage("Terms of Service", "terms.html");

// Each tag is a distinct module, so a test gets its own isolate-level caches.
export async function importWorker(tag, hostname = "flyingobject.ai") {
  const source = sourceTemplate
    .replace(
      '"__LANDING_HTML__"',
      JSON.stringify(landingPage(hostname)),
    )
    .replace('"__FAVICON_SVG__"', JSON.stringify(FAVICON_SVG))
    .replace('"__FAVICON_DARK_SVG__"', JSON.stringify(FAVICON_DARK_SVG))
    .replace('"__PRIVACY_HTML__"', JSON.stringify(PRIVACY_PAGE))
    .replace('"__TERMS_HTML__"', JSON.stringify(TERMS_PAGE))
    .replace('"__WAITLIST_SENDER__"', JSON.stringify("no-reply@flyingobject.ai"));
  const tagged = `${source}\n// ${tag}`;
  return (await import(`data:text/javascript;base64,${Buffer.from(tagged).toString("base64")}`))
    .default;
}

export function d1(database = new DatabaseSync(":memory:")) {
  return {
    database,
    prepare(sql) {
      let args = [];
      return {
        bind(...bound) {
          args = bound;
          return this;
        },
        async run() {
          return { meta: database.prepare(sql).run(...args) };
        },
        async first() {
          const row = database.prepare(sql).get(...args);
          return row === undefined ? null : { ...row };
        },
      };
    },
    async batch(statements) {
      database.exec("begin");
      try {
        for (const statement of statements) await statement.run();
        database.exec("commit");
      } catch (error) {
        database.exec("rollback");
        throw error;
      }
    },
  };
}
