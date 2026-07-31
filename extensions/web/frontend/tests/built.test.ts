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

class StreamStub {
  static opened: string[] = [];
  constructor(url: string) {
    StreamStub.opened.push(url);
  }
  addEventListener() {}
  close() {}
}

beforeEach(() => {
  StreamStub.opened = [];
  document.body.innerHTML = builtPage().replace(/<script[\s\S]*?<\/script>/g, "");
  vi.stubGlobal("EventSource", StreamStub);
});

test("the built page names a hashed module and stylesheet under this surface", () => {
  const page = builtPage();
  for (const ref of page.matchAll(/(?:src|href)="([^"]+)"/g)) {
    expect(ref[1].startsWith("/surface/web/")).toBe(true);
  }
  expect(/assets\/index-[A-Za-z0-9_-]+\.js/.test(page)).toBe(true);
  expect(/assets\/index-[A-Za-z0-9_-]+\.css/.test(page)).toBe(true);
});

test("the built bundle boots against a browser DOM and shows the token card when unauthenticated", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("unauthorized", { status: 401 }))
  );
  const card = document.getElementById("token-card");
  expect(card).not.toBeNull();

  new Function(bundle())();
  await new Promise((resolve) => setTimeout(resolve, 0));

  expect(card!.style.display).toBe("block");
  expect(document.querySelector("nav")!.hidden).toBe(true);
});

test("the built bundle renders the agents it is served and streams the turn it posts", async () => {
  const agentId = "11111111-1111-4111-8111-111111111111";
  const turnId = "33333333-3333-4333-8333-333333333333";
  const agents = {
    member: { email: "member@example.com", admin: false },
    agents: [{ id: agentId, name: "assistant", model: "opus" }],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.endsWith("/api/agents")) return Response.json(agents);
      if (url.endsWith("/chat")) return Response.json({ turn_id: turnId });
      return Response.json({ messages: [] });
    })
  );

  new Function(bundle())();
  await new Promise((resolve) => setTimeout(resolve, 0));

  const listed = document.getElementById("agents")!.querySelectorAll("button");
  expect(listed.length).toBe(1);
  expect(listed[0].textContent).toContain("assistant");

  (document.getElementById("msg") as HTMLInputElement).value = "hello";
  document.getElementById("composer")!.dispatchEvent(new Event("submit"));
  await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));

  expect(StreamStub.opened).toEqual([`/surface/web/turns/${turnId}/stream`]);
});
