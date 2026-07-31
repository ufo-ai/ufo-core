import { readFileSync } from "node:fs";
import { join } from "node:path";
import { beforeEach, expect, test, vi } from "vitest";

const STATIC = join(import.meta.dirname, "..", "..", "ufo_ext_web", "static");
const builtPage = () => readFileSync(join(STATIC, "index.html"), "utf8");

const bundle = () => {
  const asset = /src="\/surface\/web\/static\/(assets\/[^"]+\.js)"/.exec(builtPage());
  if (!asset) throw new Error("the built page references no module");
  return readFileSync(join(STATIC, asset[1]), "utf8");
};

const AGENTS = {
  member: { email: "member@example.com", admin: false },
  agents: [
    { id: "11111111-1111-4111-8111-111111111111", name: "first", model: "opus" },
    { id: "22222222-2222-4222-8222-222222222222", name: "second", model: "opus" },
  ],
};

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

const tabNamed = (name: string) =>
  [...document.getElementById("tabs")!.querySelectorAll("button")].find(
    (button) => button.textContent === name
  );

const agentNamed = (name: string) =>
  [...document.getElementById("agents")!.querySelectorAll("button")].find((button) =>
    button.textContent?.startsWith(name)
  );

beforeEach(() => {
  document.body.innerHTML = builtPage().replace(/<script[\s\S]*?<\/script>/g, "");
  location.hash = "";
});

const STALE = "stale-skill";

/** Supersede a skills read while its body parses — the fence arm that guards the paint. */
const paintedAfterSwitch = async () => {
  let release: (() => void) | null = null;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  const stale = { skills: [{ name: STALE, origin: "member", digest: "from the first agent" }] };

  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.endsWith("/api/agents")) return Response.json(AGENTS);
      if (url.includes("/11111111-1111-4111-8111-111111111111/skills")) {
        return {
          ok: true,
          status: 200,
          json: async () => {
            await held;
            return stale;
          },
        };
      }
      if (url.includes("/skills")) return Response.json({ skills: [] });
      return Response.json({ messages: [] });
    })
  );

  new Function(bundle())();
  await settle();
  agentNamed("first")!.dispatchEvent(new Event("click"));
  await settle();
  tabNamed("skills")!.dispatchEvent(new Event("click"));
  await settle();

  agentNamed("second")!.dispatchEvent(new Event("click"));
  await settle();

  release!();
  await settle();
  await settle();

  return document.getElementById("panel")!.textContent ?? "";
};

test("a skills read superseded while its body is parsing never paints", async () => {
  expect(await paintedAfterSwitch()).not.toContain(STALE);
});
