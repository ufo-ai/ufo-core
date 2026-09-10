// @vitest-environment node
// Loading the config runs esbuild, which needs the real platform globals jsdom replaces.
import { readFileSync, readdirSync } from "node:fs";
import { join, resolve } from "node:path";

import { expect, test } from "vitest";
import { loadConfigFromFile, type IndexHtmlTransformContext, type ProxyOptions } from "vite";

import { outsideRootPaths } from "../dev-routing";

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
    "views/AppsIndex.tsx",
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

test("every dev server may serve the whole repository, resolved from its own root", async () => {
  // vite resolves `server.fs.allow` against `root`, and the three roots sit at two depths: the app
  // entries live in their extensions and the brand marks under `assets/`, both above every root.
  const frontend = join(import.meta.dirname, "..");
  const repository = resolve(frontend, "..", "..", "..");
  for (const [config, root] of [
    ["vite.config.ts", frontend],
    ["sidebar/vite.config.ts", join(frontend, "sidebar")],
    ["vite.apps.config.ts", join(frontend, "apps")],
  ]) {
    const loaded = await loadConfigFromFile(
      { command: "serve", mode: "development" },
      join(frontend, config)
    );
    if (!loaded) throw new Error(`${config} did not load`);
    const allow = loaded.config.server?.fs?.allow ?? [];
    expect(allow.map((dir) => resolve(root, dir))).toEqual([repository]);
  }
});

test("the app pages serve from source behind the ingress relay", async () => {
  const loaded = await loadConfigFromFile(
    { command: "serve", mode: "development" },
    join(import.meta.dirname, "..", "vite.apps.config.ts")
  );
  if (!loaded) throw new Error("vite.apps.config.ts did not load");
  // The ingress dials this server by its own name, which vite's host check refuses.
  expect(loaded.config.server?.allowedHosts).toBe(true);
  expect(loaded.config.cacheDir).toBe(join(import.meta.dirname, "..", "node_modules", ".vite-apps"));
  const aliases = loaded.config.resolve?.alias;
  if (!Array.isArray(aliases)) throw new Error("the app pages declare no ordered aliases");
  expect(aliases[0]).toEqual({ find: "ufo/kit/jsx-dev-runtime", replacement: "react/jsx-dev-runtime" });
});

test("every page's paths that climb out of the root are served through /@fs while developing", async () => {
  for (const config of ["vite.config.ts", "sidebar/vite.config.ts", "vite.apps.config.ts"]) {
    const loaded = await loadConfigFromFile(
      { command: "serve", mode: "development" },
      join(import.meta.dirname, "..", config)
    );
    if (!loaded) throw new Error(`${config} did not load`);
    const names = (loaded.config.plugins ?? []).flat().map((plugin) => (plugin as { name: string }).name);
    expect(names).toContain("outside-root-paths");
  }
  const plugin = outsideRootPaths();
  const transform = plugin.transformIndexHtml as { handler: (html: string, ctx: IndexHtmlTransformContext) => string };
  const frontend = join(import.meta.dirname, "..");
  const page = join(frontend, "sidebar", "sidebar.html");
  const shell = transform.handler(readFileSync(page, "utf8"), { filename: page } as IndexHtmlTransformContext);
  // Root-relative, not under the base: vite's own html transform prefixes the base after this one.
  expect(shell).toContain(`href="/@fs${join(frontend, "..", "..", "..", "assets", "brand", "ufo-mark.svg")}"`);
  expect(shell).toContain('src="/src/main.tsx"');
  const app = join(frontend, "apps", "chat", "index.html");
  const framed = transform.handler(readFileSync(app, "utf8"), { filename: app } as IndexHtmlTransformContext);
  expect(framed).toContain(`src="/@fs${join(frontend, "..", "..", "app_chat", "ufo_ext_app_chat", "skills", "app-chat-home", "app.tsx")}"`);
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
