// @vitest-environment node
// Loading the config runs esbuild, which needs the real platform globals jsdom replaces.
import { join } from "node:path";

import { expect, test } from "vitest";
import { loadConfigFromFile, type ProxyOptions } from "vite";

/**
 * The dev proxy as Vite itself resolves it. `dev-routing.ts` owns where one request goes and its
 * own suite pins that; these assertions own the object that consumes it, so a rule that stops
 * asking the predicate, stops covering the whole surface, or lets the sign-in redirect leave this
 * origin cannot pass while the predicate's suite stays green.
 */
const proxyRule = async (): Promise<[string, ProxyOptions]> => {
  const loaded = await loadConfigFromFile(
    { command: "serve", mode: "development" },
    join(import.meta.dirname, "..", "vite.config.ts")
  );
  if (!loaded) throw new Error("the vite config did not load");
  const proxy = loaded.config.server?.proxy ?? {};
  const entries = Object.entries(proxy);
  expect(entries).toHaveLength(1);
  const [pattern, rule] = entries[0];
  return [pattern, rule as ProxyOptions];
};

test("one rule covers the whole surface, so no route reaches the fleet unrouted", async () => {
  const [pattern] = await proxyRule();
  expect(pattern).toBe("^/surface/web");
});

test("the sign-in redirect comes back on the dev origin", async () => {
  const [, rule] = await proxyRule();
  // `open_session` answers a 303 rebuilt from the forwarded Host. Rewriting the origin sends the
  // developer to the backend, which serves the gitignored built tree this server exists to avoid.
  expect(rule.changeOrigin).toBe(false);
  expect(rule.target).toBe("http://localhost:8710");
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
});
