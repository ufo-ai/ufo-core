import { expect, test } from "vitest";

import { devLocalPath } from "../dev-routing";

const routed = (method: string, url: string) => devLocalPath(method, url) ?? "fleet";

test("a page navigation is served by this server, not the fleet's built tree", () => {
  for (const url of ["/surface/web", "/surface/web/", "/surface/web#/agents/x", "/surface/web?a=1"]) {
    expect(routed("GET", url)).toBe("/index.html");
  }
});

test("the module under edit is served by this server", () => {
  expect(routed("GET", "/surface/web/static/src/main.tsx")).toBe(
    "/surface/web/static/src/main.tsx"
  );
  expect(routed("GET", "/surface/web/static")).toBe("/surface/web/static");
});

test("the token post and every read reach the fleet", () => {
  expect(routed("POST", "/surface/web")).toBe("fleet");
  for (const url of [
    "/surface/web/api/agents",
    "/surface/web/api/admin",
    "/surface/web/agents/x/transcript",
    "/surface/web/agents/x/intents",
    "/surface/web/workspace/sources",
    "/surface/web/turns/x/stream",
    "/surface/web/credentials",
  ]) {
    expect(routed("GET", url)).toBe("fleet");
    expect(routed("POST", url)).toBe("fleet");
  }
});
