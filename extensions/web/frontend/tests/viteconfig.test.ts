// @vitest-environment node
// Loading the config runs esbuild, which needs the real platform globals jsdom replaces.
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "vitest";
import { loadConfigFromFile, type ProxyOptions } from "vite";

/**
 * The dev proxy as Vite itself resolves it. `dev-routing.ts` owns where one request goes and its
 * own suite pins that; these assertions own the object that consumes it, so a rule that stops
 * asking the predicate, stops covering the whole surface, or lets the sign-in redirect leave this
 * origin cannot pass while the predicate's suite stays green.
 */
const proxyRule = async (config = "vite.config.ts"): Promise<[string, ProxyOptions]> => {
  const loaded = await loadConfigFromFile(
    { command: "serve", mode: "development" },
    join(import.meta.dirname, "..", config)
  );
  if (!loaded) throw new Error(`${config} did not load`);
  const proxy = loaded.config.server?.proxy ?? {};
  const entries = Object.entries(proxy);
  expect(entries).toHaveLength(1);
  const [pattern, rule] = entries[0];
  return [pattern, rule as ProxyOptions];
};

test("one rule covers the whole surface, so no route reaches the fleet unrouted", async () => {
  const [pattern] = await proxyRule();
  expect(pattern).toBe("^/(surface/web|ext/)");
});

test("the sign-in redirect comes back on the dev origin", async () => {
  const [, rule] = await proxyRule();
  // `open_session` answers a 303 rebuilt from the forwarded Host. Rewriting the origin sends the
  // developer to the backend, which serves the gitignored built tree this server exists to avoid.
  expect(rule.changeOrigin).toBe(false);
  expect(rule.target).toBe("http://localhost:8710");
});

test("the sidebar shell's page is named under the base the server publishes", async () => {
  // Vite's base middleware redirects `/index.html` into `base` and answers a 404 for any other
  // html path outside it, so a bare `/sidebar.html` leaves `/surface/web` with no page at all.
  const [, rule] = await proxyRule("sidebar/vite.config.ts");
  const bypass = rule.bypass;
  if (!bypass) throw new Error("the rule declares no bypass");
  const routed = (method: string, url: string) =>
    bypass({ method, url } as never, undefined as never, undefined as never);
  expect(routed("GET", "/surface/web")).toBe("/surface/web/static/sidebar.html");
  expect(routed("GET", "/surface/web/static/src/main.tsx")).toBe(
    "/surface/web/static/src/main.tsx"
  );
  expect(routed("GET", "/surface/web/api/agents")).toBeUndefined();
});

test("both shells send their reads to the fleet UFO_SERVE_ORIGIN names", async () => {
  // `make web` points the dev server at the Docker stack's published serve port with this
  // variable, so a hard-coded target would leave that loop reading a fleet that is not running.
  process.env.UFO_SERVE_ORIGIN = "http://ufo-2.localhost:18810";
  try {
    for (const config of ["vite.config.ts", "sidebar/vite.config.ts"]) {
      const [, rule] = await proxyRule(config);
      expect(rule.target).toBe("http://ufo-2.localhost:18810");
    }
  } finally {
    delete process.env.UFO_SERVE_ORIGIN;
  }
});

test("the rule routes each request through the predicate", async () => {
  const [, rule] = await proxyRule();
  const bypass = rule.bypass;
  if (!bypass) throw new Error("the rule declares no bypass");
  const routed = (method: string, url: string) =>
    bypass({ method, url } as never, undefined as never, undefined as never);
  expect(routed("GET", "/surface/web")).toBe("/index.html");
  expect(routed("GET", "/surface/web/static/src/main.tsx")).toBe(
    "/surface/web/static/src/main.tsx"
  );
  expect(routed("GET", "/surface/web/static")).toBe("/surface/web/static");
  expect(routed("POST", "/surface/web")).toBeUndefined();
  expect(routed("GET", "/surface/web/api/agents")).toBeUndefined();
  expect(routed("GET", "/ext/metronome/billing")).toBeUndefined();
});

/**
 * The apps tree is one directory the pages build owns and the SDK build adds to, published whole
 * under `apps/<digest>/` and read whole by the web surface. The digest covers every file, so a
 * second build that emptied the directory, or a run of the two in the other order, would publish a
 * tree missing either the pages or the module a forked page is built against — and no page would
 * fail until a member forked one.
 */
test("the pages and the SDK build into the trees that serve and materialize them", async () => {
  const loaded = async (config: string) => {
    const found = await loadConfigFromFile(
      { command: "build", mode: "production" },
      join(import.meta.dirname, "..", config)
    );
    if (!found) throw new Error(`${config} did not load`);
    return found.config.build ?? {};
  };
  const apps = await loaded("vite.apps.config.ts");
  const sdk = await loaded("vite.sdk.config.ts");
  expect(apps.outDir).toBe(join(import.meta.dirname, "..", "..", "ufo_ext_web", "apps"));
  expect(sdk.outDir).toBe(
    join(import.meta.dirname, "..", "..", "..", "sites", "ufo_ext_sites", "page", "kit")
  );
  expect(apps.emptyOutDir).toBe(true);
  expect(sdk.emptyOutDir).toBe(true);

  const script = JSON.parse(
    readFileSync(join(import.meta.dirname, "..", "package.json"), "utf8")
  ).scripts.build as string;
  for (const config of ["vite.apps.config.ts", "vite.sdk.config.ts"]) {
    expect(script.indexOf(config)).toBeGreaterThan(-1);
  }
});
