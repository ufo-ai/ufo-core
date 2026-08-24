import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { AGENT_ICONS, AgentIcon } from "@/lib/agentIcon";
import { ELEMENT_ICONS } from "@/lib/elementIcons";

/** The sprites `vite.config.ts` cut, at the URLs it resolved them to. */
declare const __MARK_SPRITES__: Record<string, string>;

const SPRITE_A = new RegExp(`^${import.meta.env.BASE_URL}assets/tabler-a-[0-9a-f]{8}\\.svg$`);

const STATIC = join(import.meta.dirname, "..", "..", "ufo_ext_web", "static");
/** The brand's own artwork, where the brand keeps it: the portal wears these files rather than a
 *  copy of what they draw. */
const BRAND = join(import.meta.dirname, "..", "..", "..", "..", "assets", "brand");
const BRAND_MARK = "ufo-mark.svg";
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
  expect(asked).toEqual([__MARK_SPRITES__.a]);
  expect(__MARK_SPRITES__.a).toMatch(SPRITE_A);
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

  expect(asked).toEqual([__MARK_SPRITES__.w]);
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

/** A sprite the page cannot read is the other case, and it is not data: the URL is one this bundle
 *  resolved for a file it emitted, so nothing but a build that lost the file answers it. That raises
 *  where the mark was asked for. It hid here before, behind a caught fetch and a console line, as
 *  every mark under the letter drawn from no paths at all — which is what a build whose marks were
 *  unreachable looked like from the outside. */
test("a sprite the page cannot read draws no mark, and the row around it stands", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("not found", { status: 404 })));
  const logged = vi.spyOn(console, "error").mockImplementation(() => {});

  render(
    <>
      <AgentIcon name="quote" />
      <span>Quotes</span>
    </>,
  );

  await waitFor(() => expect(logged).toHaveBeenCalledWith("no marks were served for q"));
  expect(screen.getByText("Quotes")).not.toBeNull();
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
  expect(slugs).toHaveLength(40);
  expect(slugs.slice(0, 4)).toEqual(["propylon", "nabatu", "gibil", "adyton"]);
  // The set is the element pack, whole and in the pack module's own order, so the picker offers no
  // mark the pack does not draw and holds no mark of tabler's own.
  expect(slugs).toEqual(Object.keys(ELEMENT_ICONS));

  const { container } = render(
    <>
      {slugs.map((slug) => (
        <AgentIcon key={slug} name={slug} />
      ))}
    </>,
  );

  for (const slug of slugs)
    expect(container.querySelector(`.element-icon-${slug}`)).not.toBeNull();
  expect(asked).toEqual([]);
});

/** The product's own mark is reserved: the picker offers it to nobody, while the main agent's row
 *  goes on holding it. That row draws the brand's own three-dot mark — the glyph the wordmark opens
 *  with, worn as a mask so it takes the ink around it — and asks for no sprite. */
test("the reserved product mark is offered nowhere and draws the brand's own mark", () => {
  const asked = servesSprites();
  const faults = vi.spyOn(console, "error").mockImplementation(() => {});

  expect(Object.keys(AGENT_ICONS)).not.toContain("ufo");

  const { container } = render(<AgentIcon name="ufo" />);

  const mark = container.querySelector<HTMLElement>(".brand-mark")!;
  expect(mark).not.toBeNull();
  expect(mark.style.mask).toContain("ufo-mark.svg");
  expect(mark.className.split(" ")).toContain("bg-current");
  expect(mark.getAttribute("aria-hidden")).toBe("true");
  expect(container.querySelector(".tabler-icon-question-mark")).toBeNull();
  expect(asked).toEqual([]);
  expect(faults.mock.calls).toHaveLength(0);
});

/** The artwork is the brand's own file, read from where the brand keeps it: the favicon links are
 *  cut from the same file, so the mark a row draws cannot drift from the mark on the tab. The saucer
 *  the portal drew for the main agent before is gone from the bundle with it. */
test("the reserved mark wears the brand file the tab icons are cut from, and no saucer", () => {
  const { container } = render(<AgentIcon name="ufo" />);
  const mask = container.querySelector<HTMLElement>(".brand-mark")!.style.mask;

  expect(mask).toContain("assets/brand/" + BRAND_MARK);
  expect(existsSync(join(BRAND, BRAND_MARK))).toBe(true);
  // The build emits one file for both readers: the favicon link and the drawn mark name it.
  const favicon = /assets\/ufo-mark-[A-Za-z0-9_-]+\.svg/.exec(
    readFileSync(join(STATIC, "index.html"), "utf8"),
  )![0];
  expect(entry().includes(favicon)).toBe(true);
  // `includes` rather than a matcher on the whole bundle: a failure should name the mark, not print
  // a megabyte of minified module.
  expect(entry().includes(OUTLINE.ufo[0][1].d)).toBe(false);
});

/** The mark sizes like every other: the glyph size unless its caller sets another. */
test("the reserved mark draws at the glyph size unless its caller sets another", () => {
  const bundled = render(<AgentIcon name="ufo" />);
  const sized = render(<AgentIcon name="ufo" className="size-8" />);

  const classes = (from: HTMLElement) =>
    from.querySelector<HTMLElement>(".brand-mark")!.className.split(" ");
  expect(classes(bundled.container)).toContain("size-(--size-glyph)");
  expect(classes(sized.container)).toContain("size-8");
  expect(classes(sized.container)).not.toContain("size-(--size-glyph)");
});

/** A mark whose paths were copied short or dropped still draws, so nothing but reading the paths
 *  back off what each one renders catches it. The floor on length holds only for the pack's own
 *  marks: tabler draws a mark from short segments as readily as from one long outline. */
test("every mark the picker offers draws paths of its own", () => {
  const asked = servesSprites();

  for (const slug of Object.keys(AGENT_ICONS)) {
    const { container } = render(<AgentIcon name={slug} />);
    const drawn = [...container.querySelectorAll("path")].map((path) => path.getAttribute("d"));
    expect(drawn.length, slug).toBeGreaterThan(0);
    for (const d of drawn) expect(d, slug).toMatch(/^[Mm]/);
  }
  for (const slug of Object.keys(ELEMENT_ICONS)) {
    const { container } = render(<AgentIcon name={slug} />);
    const drawn = [...container.querySelectorAll("path")];
    for (const path of drawn) expect(path.getAttribute("d")!.length, slug).toBeGreaterThan(40);
  }
  expect(asked).toEqual([]);
});

test("an element mark fills itself with the ink around it, and states no colour of its own", () => {
  const { container } = render(<AgentIcon name="propylon" />);

  const svg = container.querySelector("svg")!;
  expect(svg.getAttribute("fill")).toBe("currentColor");
  expect(svg.getAttribute("stroke")).toBeNull();
  expect(svg.getAttribute("class")!.split(" ")).toContain("element-icon");
  expect(container.innerHTML).not.toMatch(/#[0-9a-fA-F]{3}|rgba?\(|hsla?\(|oklch\(/);
});

/** The pack's art fills only part of the 64-unit canvas it was drawn on, and a different part per
 *  mark, so every mark drawn on the whole canvas reads lighter than the tabler marks beside it and
 *  no two read alike. Each mark instead carries a square window cropped to its own glyph. The
 *  window is the only thing that crops — the path data is the source's own, so nothing is lost.
 *  Two marks sharing a window would draw at least one of them at the wrong weight, and nothing
 *  else in the tree would say so. */
test("each element mark carries its own square window inside the pack's canvas", () => {
  const windows = new Set<string>();
  const slugs = Object.keys(ELEMENT_ICONS);

  for (const slug of slugs) {
    const { container } = render(<AgentIcon name={slug} />);
    const box = container.querySelector("svg")!.getAttribute("viewBox")!;
    const [x, y, width, height] = box.split(" ").map(Number);
    expect([x, y, width, height].every(Number.isFinite), slug).toBe(true);
    expect(width, slug).toBe(height);
    expect(width, slug).toBeLessThan(64);
    expect(x, slug).toBeGreaterThanOrEqual(0);
    expect(y, slug).toBeGreaterThanOrEqual(0);
    expect(x + width, slug).toBeLessThanOrEqual(64);
    expect(y + height, slug).toBeLessThanOrEqual(64);
    windows.add(box);
  }

  expect(windows.size).toBe(slugs.length);
});

test("the bundle carries the marks the picker offers and none of the rest", () => {
  const bundle = entry();
  const { container } = render(<AgentIcon name="propylon" />);
  const drawn = container.querySelector("path")!.getAttribute("d")!;

  expect(bundle.includes(drawn)).toBe(true);
  // `rocket` is a mark the picker offered before the element pack and offers no longer, so its
  // paths belong to the sprite now and not to the bundle.
  expect(bundle.includes(OUTLINE.rocket[0][1].d)).toBe(false);
  expect(bundle.includes(OUTLINE.anchor[0][1].d)).toBe(false);
  expect(bundle.includes(OUTLINE.zeppelin[0][1].d)).toBe(false);
});

test("a mark draws at the glyph size unless its caller sets another", async () => {
  servesSprites();

  const bundled = render(<AgentIcon name="propylon" />);
  const sized = render(<AgentIcon name="propylon" className="size-8" />);
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
