import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { Table, Td } from "@/components/ui/table";
import { BRAND_MARKS } from "@/lib/brandMark";
import { Notice } from "@/kernel/panel";
import { MessageLog, TranscriptScroll } from "@/kernel/messages";

import { AGENT, CHAT_ROW, chatsOnWire, CONVO_ID, json, MEMBER, saying, StreamFake, TURN_ID, useStreamFake, wire } from "./harness";

const STATIC = join(import.meta.dirname, "..", "..", "..", "ufo_ext_web", "static");
const BRAND = join(import.meta.dirname, "..", "..", "..", "..", "..", "assets", "brand");

/** Read once for the whole file: re-reading costs two disk reads of a sheet this size per test —
 *  enough, across the assertions below, to run one of them out of its budget on a loaded machine. */
let sheet: string | null = null;
let packed: string | null = null;

const builtStyles = (): string => {
  if (sheet !== null) return sheet;
  const page = readFileSync(join(STATIC, "sidebar.html"), "utf8");
  const asset = /href="\/surface\/web\/static\/(assets\/[^"]+\.css)"/.exec(page);
  if (!asset) throw new Error("the built page references no stylesheet");
  sheet = readFileSync(join(STATIC, asset[1]), "utf8");
  return sheet;
};

const packedStyles = (): string => {
  if (packed !== null) return packed;
  packed = builtStyles().replace(/\s+/g, "");
  return packed;
};

/** The sheet is walked once: a pattern asked to find a selector and its body in one match retraces the
 *  whole sheet from every place it could start, which on a sheet this size costs seconds. */
const rules = () =>
  packedStyles()
    .split("}")
    .map((chunk) => {
      const brace = chunk.lastIndexOf("{");
      return { selector: chunk.slice(0, brace), body: chunk.slice(brace + 1) };
    });

const HUELESS = /#0000\b/g;

const PROBES = /@supports\s*\([^{]*\)/g;

const NAMED = `aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue
blueviolet brown burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan
darkblue darkcyan darkgoldenrod darkgray darkgreen darkgrey darkkhaki darkmagenta darkolivegreen
darkorange darkorchid darkred darksalmon darkseagreen darkslateblue darkslategray darkslategrey
darkturquoise darkviolet deeppink deepskyblue dimgray dimgrey dodgerblue firebrick floralwhite
forestgreen fuchsia gainsboro ghostwhite gold goldenrod gray green greenyellow grey honeydew hotpink
indianred indigo ivory khaki lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral
lightcyan lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen
lightskyblue lightslategray lightslategrey lightsteelblue lightyellow lime limegreen linen magenta
maroon mediumaquamarine mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue
mediumspringgreen mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin
navajowhite navy oldlace olive olivedrab orange orangered orchid palegoldenrod palegreen
paleturquoise palevioletred papayawhip peachpuff peru pink plum powderblue purple rebeccapurple red
rosybrown royalblue saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue slateblue
slategray slategrey snow springgreen steelblue tan teal thistle tomato turquoise violet wheat white
whitesmoke yellow yellowgreen`
  .split(/\s+/)
  .join("|");

const AUTHORED = new RegExp(
  `#[0-9a-fA-F]{3,8}|rgba?\\(|hsla?\\(|oklch\\(|lab\\(|lch\\(|light-dark\\(` +
    `|(?<=[:,(\\s])(?:${NAMED})(?=[;,)\\s}!])`,
  "g",
);

const PALETTE_STEP =
  /--(?:bkgd-\d+|text-(?:primary|secondary|tertiary)|accent-(?:primary|secondary|live)|color-fill-ink|color-(?:live|blocked)):(?:light-dark\(#[0-9a-f]{3,6},#[0-9a-f]{3,6}\)|#[0-9a-f]{3,6})/g;

const authoredColours = (css: string) =>
  css.replace(PROBES, "").replace(HUELESS, "").match(AUTHORED) ?? [];

const outsideThePalette = (css: string) => css.replace(PALETTE_STEP, "");

beforeEach(() => {
  location.hash = "";
  useStreamFake();
});

test("every colour the portal paints resolves through the palette's eight steps", () => {
  const css = builtStyles();

  expect(authoredColours(outsideThePalette(css))).toEqual([]);
  const steps = {
    "--bkgd-100": "light-dark(#faf9f7,#191a1a)",
    "--bkgd-200": "light-dark(#f4f3f2,#262929)",
    "--bkgd-300": "light-dark(#ebeae9,#323535)",
    "--text-primary": "light-dark(#191a1a,#f5f5f5)",
    "--text-secondary": "light-dark(#676767,#a7a9a9)",
    "--text-tertiary": "light-dark(#919090,#7d7f7f)",
    "--accent-primary": "#0095ff",
    "--accent-secondary": "#ff6700",
    "--accent-live": "#16a34a",
  };
  for (const [step, value] of Object.entries(steps)) {
    expect(css.replace(/\s+/g, "")).toContain(`${step}:${value}`);
  }
  const basis = {
    "--color-surface": String.raw`var\(--bkgd-100\)`,
    "--color-ink": String.raw`var\(--text-primary\)`,
    "--color-ink-soft": String.raw`var\(--text-secondary\)`,
    "--color-ink-quiet": String.raw`var\(--text-tertiary\)`,
    "--color-field": String.raw`var\(--bkgd-200\)`,
    "--color-edge": String.raw`var\(--bkgd-300\)`,
    "--color-fill": String.raw`var\(--bkgd-200\)`,
    "--color-said": String.raw`color-mix\(in srgb, var\(--accent-primary\) 15%, var\(--bkgd-100\)\)`,
    "--color-fill-ink": "#191a1a",
    "--color-link": String.raw`color-mix\(in srgb, var\(--accent-primary\) 70%, var\(--text-primary\)\)`,
    "--color-attention-ink": String.raw`color-mix\(in srgb, var\(--accent-secondary\) 70%, var\(--text-primary\)\)`,
    "--color-live": String.raw`var\(--accent-live\)`,
  };
  for (const [token, step] of Object.entries(basis)) {
    expect(new RegExp(`${token}:\\s*${step}`).test(css)).toBe(true);
  }
  const hues = { "--color-blocked": "#eab308" };
  for (const [token, value] of Object.entries(hues)) {
    expect(packedStyles()).toContain(`${token}:${value}`);
  }
});

test("color-scheme carries the scheme, and the appearance class pins it", () => {
  const css = packedStyles();

  expect(css).toContain("color-scheme:lightdark");
  expect(css).toContain(":root.light{color-scheme:light}");
  expect(css).toContain(":root.dark{color-scheme:dark}");
  expect(css).not.toContain("@media(prefers-color-scheme:dark){:root{--");
  expect(css).toMatch(/@media\(prefers-color-scheme:dark\)\{[^{]*:where\(:not\(\.light,\.light\*\)\)/);
  expect(css).toContain(":where(.dark,.dark*)");
});

test("the page height tracks the visible viewport and respects device insets", () => {
  const css = packedStyles();
  expect(css).toContain(".h-dvh{height:100dvh}");
  expect(css).toContain("box-sizing:border-box;height:100dvh;padding-top:env(safe-area-inset-top)");
  expect(css).toContain("env(safe-area-inset-bottom)");
  const page = readFileSync(join(STATIC, "sidebar.html"), "utf8");
  expect(page).toContain("viewport-fit=cover");
});

test("reply headings carry an emitted scale, not just declared tokens", () => {
  const css = packedStyles();
  expect(css).toContain(":where(h2){font-size:var(--text-subtitle)");
  expect(css).toContain(":where(h4,h5,h6){font-size:var(--text-ui)");
});

test("a reply flows on the typeset register alone, and a code block sits snug in it", () => {
  const css = packedStyles();
  expect(css).toContain(".typeset>:where(:not(:last-child)){margin-block-end:0}");
  // Streamdown's `space-y-4` zeroes margin-block-start on the same children from the utilities layer, so
  // the register's start margins must be restated with unlayered strength at the root.
  expect(css).toContain(
    ".typeset>:where(p,ul,ol,blockquote,pre,table,img,hr){margin-block-start:var(--typeset-flow)}",
  );
  expect(css).toContain(
    ".typeset>:where(h1,h2,h3,h4,h5,h6){margin-block-start:calc(var(--typeset-flow)*2)}",
  );
  expect(css).toContain(
    ".typeset>:where(:is(h1,h2,h3,h4,h5,h6)+*){margin-block-start:var(--spacing-sm)}",
  );
  expect(css).toContain(".typeset>:first-child{margin-block-start:0}");
  expect(
    /\.typeset:is\(div:has\(>pre\),div:has\(>table\),div:has\(>div>pre\),div:has\(>div>table\)\)\{[^}]*margin-block:var\(--typeset-flow\)0[^}]*\}/.test(
      css,
    ),
  ).toBe(true);
  expect(css).toContain(".typeset:is(div:has(>pre),div:has(>div>pre)):where(pre){margin-block:0}");
  expect(css).toContain(
    '.typeset:where([data-streamdown=code-block-header][data-language=""]){display:none}',
  );
});

test("a quoted passage is dimmed by a token the theme declares", () => {
  // A name the theme never defines makes the declaration invalid, and the browser drops it — so the
  // quote would render at full weight and nothing would say why.
  expect(packedStyles()).toContain("opacity:var(--opacity-muted-soft)");
});

test("a table scrolls its own overflow instead of squeezing the page", () => {
  render(
    <Table>
      <tbody>
        <tr>
          <Td>a cell</Td>
        </tr>
      </tbody>
    </Table>,
  );

  const table = screen.getByRole("table");
  expect(table.parentElement?.className).toContain("overflow-x-auto");
  expect(table.className).not.toContain("overflow");
});

test("a table keeps its height inside a scrolling panel instead of collapsing", () => {
  render(
    <Table>
      <tbody>
        <tr>
          <Td>a cell</Td>
        </tr>
      </tbody>
    </Table>,
  );

  expect(screen.getByRole("table").parentElement?.className).toContain("shrink-0");
  expect(packedStyles()).toContain(".shrink-0{flex-shrink:0}");
});

test("a transcript bubble wraps an unbreakable string instead of widening the pane", () => {
  const blob = '{"pull_number":1269,"head_sha":"e8beb82515566586de252fdc5a411db2526057e1"}';
  render(
    <TranscriptScroll>
      <MessageLog
        messages={[
          { role: "user", text: blob },
          { role: "assistant", text: blob },
        ]}
      />
    </TranscriptScroll>,
  );

  for (const bubble of screen.getAllByText(blob)) {
    expect(bubble.closest("[data-slot=bubble-content]")!.className).toContain("wrap-anywhere");
  }
  expect(packedStyles()).toContain(".wrap-anywhere{overflow-wrap:anywhere}");
});

test("the working pulse yields to reduced motion in the built sheet", () => {
  const css = packedStyles();
  expect(/@media\(prefers-reduced-motion:reduce\)\{[^}]*\.motion-reduce\\:animate-none\{animation:none/.test(css)).toBe(true);
});

test("the waiting mark holds its threshold in the built sheet", () => {
  const css = packedStyles();
  expect(css).toContain("--delay-waiting:.25s");
  expect(css).toContain("--animate-waiting:waitingvar(--duration-waiting)ease-outvar(--delay-waiting)both");
  expect(css).toContain("@keyframeswaiting{0%{visibility:hidden;opacity:0}}");
  expect(css).toContain(".animate-waiting{animation:var(--animate-waiting)}");
});

/** The global stillness rule cuts every duration to nothing and must not cut the delay with it: the
 *  threshold is when the mark appears rather than how it moves. */
test("stillness shortens the waiting fade and leaves its threshold standing", () => {
  const css = packedStyles();
  expect(css).toContain(
    "@media(prefers-reduced-motion:reduce){*,:before,:after{transition-duration:.01ms!important;animation-duration:.01ms!important}}",
  );
  expect(css).toContain("[data-part=skeleton]{animation:var(--animate-waiting)}");
});

const BRANDS = join(import.meta.dirname, "..", "..", "src", "assets", "brands");

/** Tailwind scans this directory, so a mark's own words reach the compiler as class candidates and a
 *  colour-bearing name emits a literal. A colour function survives the data URI Vite builds; `#` does not. */
const MARK_TOKENS = /\b(?:ring|shadow|border|blur)\b|rgba?\(|hsla?\(/;
const SERVED = [".svg", ".png"];

test("a vendored mark carries nothing the theme or the surface refuses", () => {
  const offenders = readdirSync(BRANDS)
    .filter((name) => name.endsWith(".svg"))
    .filter((name) => MARK_TOKENS.test(readFileSync(join(BRANDS, name), "utf8")));
  expect(offenders).toEqual([]);
  const unserved = readdirSync(BRANDS).filter(
    (name) => !SERVED.some((suffix) => name.endsWith(suffix)),
  );
  expect(unserved).toEqual([]);
});

test("every mark the portal claims is one the built sheet can draw", () => {
  const css = packedStyles();
  const declared = new Set([...css.matchAll(/--brand-([a-z_]+):url\(/g)].map((hit) => hit[1]));
  expect([...BRAND_MARKS].filter((mark) => !declared.has(mark))).toEqual([]);
  expect([...declared].filter((name) => !BRAND_MARKS.has(name))).toEqual([]);
});

test("the reading plane's tokens survive into the built sheet", () => {
  const css = packedStyles();
  expect(css).toContain("--leading-reading:1.65");
  expect(css).toContain("--shadow-raised:");
  expect(css).toContain("box-shadow:var(--shadow-raised)");
  expect(css).toContain("--color-link:color-mix(insrgb,var(--accent-primary)70%,var(--text-primary))");
  expect(css).toContain("--color-affirm:color-mix(insrgb,var(--accent-primary)15%,var(--bkgd-100))");
  expect(css).toContain(
    "--color-attention:color-mix(insrgb,var(--accent-secondary)15%,var(--bkgd-100))",
  );
  expect(css).toContain(
    "--color-attention-ink:color-mix(insrgb,var(--accent-secondary)70%,var(--text-primary))",
  );
});

test("the bundled faces are Inter for the chrome and Roboto Mono for the code", () => {
  const css = packedStyles();
  expect(css).toContain('--font-sans:"Inter",system-ui,sans-serif');
  expect(/@font-face\{font-family:Inter;src:url\(\/surface\/web\/static\/assets\/Inter-[^)]+\.woff2\)/.test(css)).toBe(true);
  expect(css).toContain('--font-mono:"RobotoMono"');
  expect(/@font-face\{font-family:RobotoMono;src:url\(\/surface\/web\/static\/assets\/RobotoMono-[^)]+\.ttf\)/.test(css)).toBe(true);
});

test("one sans face carries the chrome, and no rule names a second family", () => {
  const css = packedStyles();
  expect(css).toContain("h1,h2,h3{text-wrap:balance}");
  expect(/h1,h2,h3\{[^}]*font-family/.test(css)).toBe(false);
  expect(css).not.toContain("--font-display");
  expect(css).not.toContain("Canela");
});

test("the shadcn contract carries the theme, and names nothing no component reads", () => {
  const css = readFileSync(join(import.meta.dirname, "..", "..", "src", "theme.css"), "utf8").replace(
    /\s+/g,
    "",
  );
  for (const token of [
    "background",
    "foreground",
    "card",
    "popover",
    "primary",
    "muted",
    "border",
    "ring",
    "sidebar",
  ]) {
    expect(css).toContain(`--color-${token}:var(--${token})`);
  }
  for (const token of ["secondary", "accent", "destructive", "success", "warning", "input"]) {
    expect(css).not.toContain(`--color-${token}:`);
  }
  expect(css).toContain("--radius:0.25rem");
  expect(css).toContain("--muted:var(--bkgd-200)");
});

test("text is smoothed and wrapped, and headings balance", () => {
  const css = packedStyles();
  expect(css).toContain("-webkit-font-smoothing:antialiased");
  expect(css).toContain("text-wrap:pretty");
  expect(css).toContain("text-wrap:balance");
});

const LAYERS = /@layer\s+([a-z]+)\s*\{/g;

/** Tailwind emits one flat block per layer, so the last block opened before a rule is the block it is
 *  in, and the order those blocks first appear in is the order that ranks them. */
const layerHolding = (css: string, at: number) => {
  const opened = [...css.matchAll(LAYERS)].filter((hit) => (hit.index ?? 0) < at);
  return opened[opened.length - 1][1];
};

test("the scrollbar thumb is drawn by scrolling and by nothing else", () => {
  const css = packedStyles();

  expect(css).toContain(":root{scrollbar-color:transparenttransparent}");
  expect(css).toContain("*{scrollbar-width:thin;transition:scrollbar-color.2svar(--ease-leave)}");
  expect(css).toContain(
    "[data-scrolling]{scrollbar-color:var(--color-edge-strong)transparent;transition-duration:0s}",
  );
  const drawn = rules().filter((rule) => rule.body.includes("scrollbar"));
  expect(drawn.filter((rule) => rule.selector.includes(":hover"))).toEqual([]);
  expect(drawn.filter((rule) => rule.selector.includes("focus-within"))).toEqual([]);
  expect(/@media\(prefers-reduced-motion:reduce\)\{[^}]*transition-duration:\.01ms!important/.test(css)).toBe(
    true,
  );
});

test("the composer says it has the cursor with the caret, and nothing else keeps a ring", () => {
  const css = packedStyles();
  expect(css.includes(":focus-visible{outline:2pxsolidvar(--ring)")).toBe(true);
  expect(css.includes("[data-field-card]textarea:focus-visible{outline:none")).toBe(true);
  expect(css.includes("[data-field-card]:has(textarea:focus-visible)")).toBe(false);
});

test("the transcript's own hush outranks the scroll mark while it scrolls itself", () => {
  const css = builtStyles();
  const order = [...css.matchAll(LAYERS)].map((hit) => hit[1]);
  expect(css).not.toMatch(/@layer[^{]*;/);

  const reveal = css.indexOf("[data-scrolling]{");
  const hush = css.indexOf(".data-autoscrolling\\:scrollbar-quiet[data-autoscrolling]{");
  expect(reveal).toBeGreaterThan(-1);
  expect(hush).toBeGreaterThan(-1);

  expect(layerHolding(css, reveal)).toBe("base");
  expect(layerHolding(css, hush)).toBe("utilities");
  expect(order.indexOf("utilities")).toBeGreaterThan(order.indexOf("base"));
});

test("a control answers the pointer, and reduced motion cuts the answer short", () => {
  const css = packedStyles();
  expect(css).toContain("active\\:scale-\\[0\\.96\\]:active{scale:.96}");
  expect(css).toContain(".bg-primary{background-color:var(--primary)}");
  expect(/@media\(prefers-reduced-motion:reduce\)\{[^}]*transition-duration:\.01ms!important/.test(css)).toBe(
    true,
  );
});

test("the field surface is drawn by the field primitives and by nothing else", () => {
  const src = join(import.meta.dirname, "..", "..", "src");
  const drawn = readdirSync(src, { recursive: true, encoding: "utf8" })
    .filter((name) => name.endsWith(".tsx") && !name.startsWith("components/ui/"))
    .filter((name) => readFileSync(join(src, name), "utf8").includes("bg-field"));

  expect(drawn).toEqual([]);
});

test("replies read as a document and member bubbles stay bubbles", async () => {
  wire({
    ...chatsOnWire([CHAT_ROW]),
    "/transcript": () =>
      json({ messages: [{ role: "user", text: "mine" }, { role: "assistant", text: "reply" }] }),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "mine" }),
  });
  location.hash = "#/c/" + CONVO_ID;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const agentSaid = (await screen.findByText("reply")).closest("[data-slot=bubble-content]")!;
  expect(agentSaid.className).toContain("leading-reading");
  const agentSide = agentSaid.closest("[data-role=agent]")!;
  expect(agentSide.className).toContain("w-full");
  expect(agentSide.className).not.toContain("bg-said");
  expect(agentSide.className).not.toContain("animate-appear");
  const mineSaid = screen.getByText("mine").closest("[data-slot=bubble-content]")!;
  expect(mineSaid.closest("[data-role=me]")!.className).toContain("self-end");
  expect(mineSaid.closest("[data-role=me]")!.className).toContain(
    "*:data-[slot=bubble-content]:bg-said",
  );
  for (const said of [agentSaid, mineSaid]) expect(said.className).toContain("wrap-anywhere");

  await userEvent.type(screen.getByLabelText("Ask UFO"), "go");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("message", { text: "streaming now" });
  const live = (await screen.findByText(saying("streaming now"))).closest("[data-role=agent]")!;
  expect(live.className).toContain("animate-appear");
  StreamFake.last().emit("terminal", { status: "done", model: "opus", tokens: 1, cost_micro_usd: 0 });
  await waitFor(() => {
    const settled = screen.getByText(saying("streaming now")).closest("[data-role=agent]")!;
    expect(settled.className).not.toContain("animate-appear");
  });
});

test("both notice tones keep the chrome type size", () => {
  const { container } = render(
    <div>
      <Notice tone="attention">refused</Notice>
      <Notice>done</Notice>
    </div>,
  );
  for (const notice of Array.from(container.querySelectorAll("div > div > div"))) {
    expect(notice.className).toContain("text-ui");
  }
});

test("the sidebar heads itself with the drawn ufo mark", async () => {
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const marks = await screen.findAllByRole("img", { name: "ufo" });
  const brand = marks.find((mark) => mark.getAttribute("style")?.includes("ufo-mark.svg"))!;
  expect(brand.getAttribute("class")).not.toContain("tracking");
  expect(brand.getAttribute("style")).toContain("ufo-mark.svg");
  expect(readFileSync(join(STATIC, "sidebar.html"), "utf8")).toContain(
    '<body data-shell="sidebar">',
  );
  expect(packedStyles()).toContain(
    'body[data-shell=sidebar]{--size-wordmark:18px;--size-logo:72px}',
  );
});

test("the favicons use the exact light and dark brand marks", () => {
  const page = readFileSync(join(STATIC, "sidebar.html"), "utf8");
  const light = /href="\/surface\/web\/static\/(assets\/ufo-mark-[^"]+\.svg)"[^>]+media="\(prefers-color-scheme: light\)"/.exec(page);
  const dark = /href="\/surface\/web\/static\/(assets\/ufo-mark-on-dark-[^"]+\.svg)"[^>]+media="\(prefers-color-scheme: dark\)"/.exec(page);
  if (!light || !dark) throw new Error("the built page references no light and dark favicons");
  expect(readFileSync(join(STATIC, light[1]), "utf8")).toBe(
    readFileSync(join(BRAND, "ufo-mark.svg"), "utf8"),
  );
  expect(readFileSync(join(STATIC, dark[1]), "utf8")).toBe(
    readFileSync(join(BRAND, "ufo-mark-on-dark.svg"), "utf8"),
  );
});

test("a field shows the member that it is disabled, or that they left it invalid", () => {
  const css = packedStyles();
  expect(css).toContain("disabled\\:cursor-not-allowed:disabled{cursor:not-allowed}");
  expect(css).toContain("user-invalid\\:border-ink:user-invalid{border-color:var(--color-ink)}");
  expect(css).toContain("user-invalid\\:border-dashed:user-invalid{");
  expect(css).not.toContain("user-invalid\\:border-attention");
});

test("a placeholder is muted rather than mistaken for a value", () => {
  expect(packedStyles()).toContain(
    ".placeholder\\:text-ink-faint::placeholder{color:var(--color-ink-faint)}",
  );
});

test("muted text is the palette's second tone, never ink held back by opacity", () => {
  const src = join(import.meta.dirname, "..", "..", "src");
  const dimmed = readdirSync(src, { recursive: true, encoding: "utf8" })
    .filter((name) => name.endsWith(".tsx") || name.endsWith(".ts"))
    .filter((name) => /opacity-\(--opacity-muted/.test(readFileSync(join(src, name), "utf8")));

  expect(dimmed.sort()).toEqual(["components/ui/button.tsx", "kernel/table.tsx"]);
});
