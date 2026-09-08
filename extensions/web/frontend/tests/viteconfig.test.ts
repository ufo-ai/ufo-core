// @vitest-environment node
// Loading the config runs esbuild, which needs the real platform globals jsdom replaces.
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "vitest";
import { loadConfigFromFile, type ProxyOptions } from "vite";

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

test("one rule covers the surface and the gateway's doors, so no route reaches the stack unrouted", async () => {
  const [pattern] = await proxyRule();
  expect(pattern).toBe("^/(surface/web|ext/|login|logout|join|v1/onboard)");
});

test("the sign-in redirect comes back on the dev origin", async () => {
  const [, rule] = await proxyRule();
  // `changeOrigin` stays off because `open_session` answers a 303 rebuilt from the forwarded Host:
  // rewritten, the sign-in lands on the backend, which serves the gitignored built tree.
  expect(rule.changeOrigin).toBe(false);
  expect(rule.target).toBe("http://localhost:8080");
});

test("the sidebar shell's page is named under the base the server publishes", async () => {
  // This root's page is `sidebar.html`, named under `base` because vite's base middleware redirects
  // `/index.html` into the base and answers a 404 for any other html path outside it.
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

test("the sidebar shell contains only its entry, navigation, and route seams", async () => {
  const source = join(import.meta.dirname, "..", "sidebar", "src");
  expect(
    readdirSync(source, { recursive: true, encoding: "utf8" })
      .filter((path) => /\.[tj]sx?$/.test(path))
      .sort()
  ).toEqual([
    "App.tsx",
    "components/Sidebar.tsx",
    "lib/rail.ts",
    "lib/railStore.ts",
    "lib/route.ts",
    "lib/router.ts",
    "lib/title.ts",
    "main.tsx",
    "views/Agents.tsx",
    "views/FirstRun.tsx",
    "views/Spotlight.tsx",
    "views/Store.tsx",
  ]);

  const loaded = await loadConfigFromFile(
    { command: "build", mode: "production" },
    join(import.meta.dirname, "..", "sidebar", "vite.config.ts")
  );
  if (!loaded) throw new Error("sidebar/vite.config.ts did not load");
  const aliases = loaded.config.resolve?.alias;
  if (!Array.isArray(aliases)) throw new Error("the sidebar declares no ordered aliases");
  expect(aliases.find(({ find }) => find === "@")?.replacement).toBe(
    join(import.meta.dirname, "..", "src")
  );
  expect(readFileSync(join(import.meta.dirname, "..", "sidebar", "sidebar.html"), "utf8")).toContain(
    'src="/src/main.tsx"'
  );
  expect(readFileSync(join(source, "main.tsx"), "utf8")).toBe('import "../../src/main";\n');
});

test("both shells send their reads to the stack origin UFO_STACK_ORIGIN names", async () => {
  process.env.UFO_STACK_ORIGIN = "http://ufo-2.localhost:18180";
  try {
    for (const config of ["vite.config.ts", "sidebar/vite.config.ts"]) {
      const [, rule] = await proxyRule(config);
      expect(rule.target).toBe("http://ufo-2.localhost:18180");
    }
  } finally {
    delete process.env.UFO_STACK_ORIGIN;
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
