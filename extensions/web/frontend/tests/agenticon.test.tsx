import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { AGENT_ICONS, AgentIcon } from "@/lib/agentIcon";

/** The sprites `vite.config.ts` cut, named as it hashed them. */
declare const __MARK_SPRITES__: Record<string, string>;

const STATIC = join(import.meta.dirname, "..", "..", "ufo_ext_web", "static");
const OUTLINE: Record<string, [string, Record<string, string>][]> = JSON.parse(
  readFileSync(
    join(import.meta.dirname, "..", "node_modules", "@tabler", "icons", "tabler-nodes-outline.json"),
    "utf8",
  ),
);

const entry = () => {
  const page = readFileSync(join(STATIC, "index.html"), "utf8");
  const asset = /src="\/surface\/web\/static\/(assets\/[^"]+\.js)"/.exec(page);
  if (!asset) throw new Error("the built page references no module");
  return readFileSync(join(STATIC, asset[1]), "utf8");
};

/** The surface, answering a sprite request with the file this build emitted. The marks a test
 *  reads back are tabler's own paths, carried the whole way: cut into a sprite by the build,
 *  served under the hashed name the bundle holds, parsed by the module under test. */
function servesSprites(): string[] {
  const asked: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      asked.push(url);
      const file = join(STATIC, url.replace(import.meta.env.BASE_URL, ""));
      if (!existsSync(file)) throw new Error(file + " is unbuilt: npm run build");
      return new Response(readFileSync(file, "utf8"), {
        headers: { "content-type": "image/svg+xml" },
      });
    }),
  );
  return asked;
}

test("a mark the picker never offers draws, from tabler's own paths", async () => {
  const asked = servesSprites();
  expect(Object.keys(AGENT_ICONS)).not.toContain("anchor");

  const { container } = render(<AgentIcon name="anchor" />);

  await waitFor(() => expect(container.querySelector("path")).not.toBeNull());
  expect([...container.querySelectorAll("path")].map((path) => path.getAttribute("d"))).toEqual(
    OUTLINE.anchor.map(([, attributes]) => attributes.d),
  );
  expect(asked).toEqual(["/surface/web/static/" + __MARK_SPRITES__.a]);
  expect(__MARK_SPRITES__.a).toMatch(/^assets\/tabler-a-[0-9a-f]{8}\.svg$/);
});

test("the mark takes the ink around it, and states no colour of its own", async () => {
  servesSprites();

  const { container } = render(<AgentIcon name="accessible" />);

  await waitFor(() => expect(container.querySelector("path")).not.toBeNull());
  const svg = container.querySelector("svg")!;
  expect(svg.getAttribute("stroke")).toBe("currentColor");
  expect(svg.getAttribute("fill")).toBe("none");
  // The one node tabler fills is filled with the same ink, not with a colour of its own.
  expect(
    [...svg.querySelectorAll("path")].map((path) => path.getAttribute("fill")),
  ).toContain("currentColor");
  expect(container.innerHTML).not.toMatch(/#[0-9a-fA-F]{3}|rgba?\(|hsla?\(|oklch\(/);
});

test("a second mark under a letter already read draws without asking again", async () => {
  const asked = servesSprites();

  const first = render(<AgentIcon name="wifi" />);
  await waitFor(() => expect(first.container.querySelector("path")).not.toBeNull());
  const second = render(<AgentIcon name="wand" />);
  await waitFor(() => expect(second.container.querySelector("path")).not.toBeNull());

  expect(asked).toEqual(["/surface/web/static/" + __MARK_SPRITES__.w]);
  expect([...second.container.querySelectorAll("path")].map((path) => path.getAttribute("d"))).toEqual(
    OUTLINE.wand.map(([, attributes]) => attributes.d),
  );
});

test("a name no sprite answers is reported and drawn as the unknown mark, and its row stands", async () => {
  servesSprites();
  const faults = vi.spyOn(console, "error").mockImplementation(() => {});

  const { container } = render(
    <div>
      <AgentIcon name="zzz-no-such-mark" />
      <span>Support</span>
    </div>,
  );

  await waitFor(() =>
    expect(container.querySelector(".tabler-icon-question-mark")).not.toBeNull(),
  );
  expect(screen.getByText("Support")).toBeTruthy();
  expect(faults.mock.calls.map(String)).toContain("no app mark is drawn for zzz-no-such-mark");
});

test("a name that could name no mark at all is answered without a fetch", () => {
  const asked = servesSprites();
  const faults = vi.spyOn(console, "error").mockImplementation(() => {});

  const { container } = render(<AgentIcon name="Rocket" />);

  expect(container.querySelector(".tabler-icon-question-mark")).not.toBeNull();
  expect(asked).toEqual([]);
  expect(faults.mock.calls.map(String)).toContain("no app mark is drawn for Rocket");
});

test("the picker's marks are the bundle's own, in the order it draws them", () => {
  const asked = servesSprites();
  const slugs = Object.keys(AGENT_ICONS);
  expect(slugs).toHaveLength(41);
  expect(slugs[0]).toBe("ufo");
  expect(slugs.slice(0, 4)).toEqual(["ufo", "robot", "rocket", "bolt"]);
  for (const slug of slugs) expect(OUTLINE[slug]).toBeTruthy();

  const { container } = render(
    <>
      {slugs.map((slug) => (
        <AgentIcon key={slug} name={slug} />
      ))}
    </>,
  );

  for (const slug of slugs) expect(container.querySelector(`.tabler-icon-${slug}`)).not.toBeNull();
  expect(asked).toEqual([]);
});

test("the bundle carries the marks the picker offers and none of the rest", () => {
  const bundle = entry();

  expect(bundle.includes(OUTLINE.rocket[0][1].d)).toBe(true);
  expect(bundle.includes(OUTLINE.anchor[0][1].d)).toBe(false);
  expect(bundle.includes(OUTLINE.zeppelin[0][1].d)).toBe(false);
});

test("a mark draws at the glyph size unless its caller sets another", async () => {
  servesSprites();

  const bundled = render(<AgentIcon name="rocket" />);
  const sized = render(<AgentIcon name="rocket" className="size-8" />);
  const fetched = render(<AgentIcon name="anchor" className="size-8" />);

  const classes = (from: HTMLElement) =>
    from.querySelector("svg")!.getAttribute("class")!.split(" ");
  expect(classes(bundled.container)).toContain("size-(--size-glyph)");
  expect(classes(sized.container)).toContain("size-8");
  expect(classes(sized.container)).not.toContain("size-(--size-glyph)");
  await waitFor(() => expect(fetched.container.querySelector("path")).not.toBeNull());
  expect(classes(fetched.container)).toContain("size-8");
  expect(classes(fetched.container)).not.toContain("size-(--size-glyph)");
});

test.each(["constructor", "valueOf", "hasOwnProperty", "toString"])(
  "a name every object already answers is not a mark the bundle holds: %s",
  async (name) => {
    servesSprites();
    vi.spyOn(console, "error").mockImplementation(() => {});

    const { container } = render(<AgentIcon name={name} />);

    await waitFor(() =>
      expect(container.querySelector(".tabler-icon-question-mark")).not.toBeNull(),
    );
  },
);

test("a name carrying what no slug may draws the unknown mark without asking for a sprite", () => {
  const asked = servesSprites();
  vi.spyOn(console, "error").mockImplementation(() => {});

  const { container } = render(<AgentIcon name="__proto__" />);

  expect(container.querySelector(".tabler-icon-question-mark")).not.toBeNull();
  expect(asked).toEqual([]);
});
