// The same substitution main.tf applies at deploy, so the tested worker is the shipped artifact.
import { readFile } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";

const moduleDir = new URL(".", import.meta.url);
export const LANDING_PAGE = await readFile(new URL("landing.html", moduleDir), "utf8");
const source = (await readFile(new URL("worker.js", moduleDir), "utf8"))
  .replace('"__LANDING_HTML__"', JSON.stringify(LANDING_PAGE))
  .replace('"__WAITLIST_SENDER__"', JSON.stringify("no-reply@flyingobject.ai"));

// Each tag is a distinct module, so a test gets its own isolate-level caches.
export async function importWorker(tag) {
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
