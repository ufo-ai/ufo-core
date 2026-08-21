import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { Table, Td } from "@/components/ui/table";
import { Notice } from "@/kernel/panel";
import { MessageLog, TranscriptScroll } from "@/kernel/messages";

import {
  CHAT_ROW,
  CONVO_ID,
  AGENT,
  MEMBER,
  StreamFake,
  TURN_ID,
  json,
  saying,
  useStreamFake,
  wire,
} from "./harness";

const STATIC = join(import.meta.dirname, "..", "..", "ufo_ext_web", "static");
const BRAND = join(import.meta.dirname, "..", "..", "..", "..", "assets", "brand");

const builtStyles = () => {
  const page = readFileSync(join(STATIC, "index.html"), "utf8");
  const asset = /href="\/surface\/web\/static\/(assets\/[^"]+\.css)"/.exec(page);
  if (!asset) throw new Error("the built page references no stylesheet");
  return readFileSync(join(STATIC, asset[1]), "utf8");
};

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
  /--(?:bkgd-\d+|text-(?:primary|secondary)|accent-(?:primary|secondary)):(?:light-dark\(#[0-9a-f]{6},#[0-9a-f]{6}\)|#[0-9a-f]{6})/g;

const authoredColours = (css: string) =>
  css.replace(PROBES, "").replace(HUELESS, "").match(AUTHORED) ?? [];

/** Everything but the palette declarations themselves — a step is where a colour may be written. */
const outsideThePalette = (css: string) => css.replace(PALETTE_STEP, "");

beforeEach(() => {
  location.hash = "";
  useStreamFake();
});

test("every colour the portal paints resolves through the palette's seven steps", () => {
  const css = builtStyles();

  expect(authoredColours(outsideThePalette(css))).toEqual([]);
  // Each step carries both schemes in one declaration, exactly as the Figma theme states them.
  const steps = {
    "--bkgd-100": "light-dark(#faf9f7,#191a1a)",
    "--bkgd-200": "light-dark(#f4f3f2,#262929)",
    "--bkgd-300": "light-dark(#ebeae9,#323535)",
    "--text-primary": "light-dark(#191a1a,#f5f5f5)",
    "--text-secondary": "light-dark(#919090,#a7a9a9)",
    "--accent-primary": "#0095ff",
    "--accent-secondary": "#ff6700",
  };
  for (const [step, value] of Object.entries(steps)) {
    expect(css.replace(/\s+/g, "")).toContain(`${step}:${value}`);
  }
  const basis = {
    "--color-surface": String.raw`var\(--bkgd-100\)`,
    "--color-ink": String.raw`var\(--text-primary\)`,
    "--color-ink-soft": String.raw`var\(--text-secondary\)`,
    "--color-field": String.raw`var\(--bkgd-200\)`,
    "--color-edge": String.raw`var\(--bkgd-300\)`,
    "--color-fill": String.raw`var\(--bkgd-200\)`,
    "--color-link": String.raw`color-mix\(in srgb, var\(--accent-primary\) 70%, var\(--text-primary\)\)`,
    "--color-attention-ink": String.raw`color-mix\(in srgb, var\(--accent-secondary\) 70%, var\(--text-primary\)\)`,
    "--color-live": String.raw`var\(--accent-primary\)`,
    "--color-blocked": String.raw`var\(--accent-secondary\)`,
  };
  for (const [token, step] of Object.entries(basis)) {
    expect(new RegExp(`${token}:\\s*${step}`).test(css)).toBe(true);
  }
});

test("color-scheme carries the scheme, and the appearance class pins it", () => {
  const css = builtStyles().replace(/\s+/g, "");

  // A member who has pinned nothing leaves the choice with the browser; the two classes the
  // appearance control writes are the only thing that overrides it, and `light-dark()` reads them.
  expect(css).toContain("color-scheme:lightdark");
  expect(css).toContain(":root.light{color-scheme:light}");
  expect(css).toContain(":root.dark{color-scheme:dark}");
  // No role is written twice to answer a mode, so no second block can drift out of step.
  expect(css).not.toContain("@media(prefers-color-scheme:dark){:root{--");
  // Streamdown's own `dark:` classes resolve through that same pinning, not through the bare query.
  expect(css).toMatch(/@media\(prefers-color-scheme:dark\)\{[^{]*:where\(:not\(\.light,\.light\*\)\)/);
  expect(css).toContain(":where(.dark,.dark*)");
});

test("the drawer fills a narrow viewport rather than overflowing it", () => {
  expect(builtStyles().replace(/\s+/g, "")).toContain("min(520px,100vw)");
});

test("the page height tracks the visible viewport and respects device insets", () => {
  const css = builtStyles().replace(/\s+/g, "");
  expect(css).toContain(".h-dvh{height:100dvh}");
  expect(css).toContain("box-sizing:border-box;height:100dvh;padding-top:env(safe-area-inset-top)");
  expect(css).toContain("env(safe-area-inset-bottom)");
  const page = readFileSync(join(STATIC, "index.html"), "utf8");
  expect(page).toContain("viewport-fit=cover");
});

test("reply headings carry an emitted scale, not just declared tokens", () => {
  const css = builtStyles().replace(/\s+/g, "");
  expect(css).toContain(":where(h2){font-size:var(--text-subtitle)");
  expect(css).toContain(":where(h4,h5,h6){font-size:var(--text-ui)");
});

test("a reply flows on the typeset register alone, and a code block sits snug in it", () => {
  // Streamdown ships its own vertical rhythm (`space-y-4` on the root, `my-4` on the block
  // wrappers, a typeset margin on the pre inside a stripped wrapper); stacked on the typeset flow
  // it opens a well of blank space over every code block. One register governs: the wrappers take
  // the typeset flow and the pre sits flush in the wrapper that places it.
  const css = builtStyles().replace(/\s+/g, "");
  expect(css).toContain(".typeset>:where(:not(:last-child)){margin-block-end:0}");
  // Streamdown's `space-y-4` zeroes margin-block-start on the same children from the utilities
  // layer, so the register's start margins must be restated with unlayered strength at the root:
  // flow for a block, doubled over a heading, tightened under one, and zero on the first child.
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
  // A fence with no language still gets Streamdown's 32px header row, empty — a band of nothing
  // over the code. A header with no name to state does not ship.
  expect(css).toContain(
    '.typeset:where([data-streamdown=code-block-header][data-language=""]){display:none}',
  );
});

test("a quoted passage is dimmed by a token the theme declares", () => {
  // A name the theme never defines makes the declaration invalid, and the browser drops it — so
  // the quote renders at full weight and nothing says why.
  expect(builtStyles().replace(/\s+/g, "")).toContain("opacity:var(--opacity-muted-soft)");
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

  // The frame scrolls on both axes, so its automatic minimum size is zero and a
  // fixed-height flex panel would otherwise shrink it away instead of scrolling.
  expect(screen.getByRole("table").parentElement?.className).toContain("shrink-0");
  expect(builtStyles().replace(/\s+/g, "")).toContain(".shrink-0{flex-shrink:0}");
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
  // `anywhere` rather than `break-word`: only `anywhere` lowers the intrinsic minimum width, so a
  // shrink-to-fit bubble resolves to the pane instead of to its 70ch maximum and overflowing it.
  expect(builtStyles().replace(/\s+/g, "")).toContain(".wrap-anywhere{overflow-wrap:anywhere}");
});

test("the working pulse yields to reduced motion in the built sheet", () => {
  const css = builtStyles().replace(/\s+/g, "");
  expect(/@media\(prefers-reduced-motion:reduce\)\{[^}]*\.motion-reduce\\:animate-none\{animation:none/.test(css)).toBe(true);
});

test("the reading plane's tokens survive into the built sheet", () => {
  const css = builtStyles().replace(/\s+/g, "");
  expect(css).toContain("--leading-reading:1.65");
  expect(css).toContain("--size-decode-cell:1ch");
  // Both channels are declared, and each mix resolves on the cell that carries `--f` rather than at
  // the root, where there is no such value to read. The weights themselves are taste and move.
  for (const end of ["--ripple-lo", "--ripple-hi", "--pulse-lo", "--pulse-hi"]) {
    expect(css).toContain(end + ":");
  }
  expect(css).toContain(
    ".decode-ripple{color:color-mix(insrgb,var(--ripple-hi)calc(var(--f)*1%),var(--ripple-lo))}",
  );
  expect(css).toContain(
    ".decode-pulse{color:color-mix(insrgb,var(--pulse-hi)calc(var(--f)*1%),var(--pulse-lo))}",
  );
  // Every colour is the palette's own steps: the ember channel names no gold of its own.
  expect(css).toContain("--pulse-hi:color-mix(insrgb,var(--accent-secondary)");
  expect(css).toContain("--shadow-raised:");
  expect(css).toContain("box-shadow:var(--shadow-raised)");
  expect(css).toContain("--color-link:color-mix(insrgb,var(--accent-primary)70%,var(--text-primary))");
  // Each accent tints the pane at one weight, so a status reads the same in both schemes.
  expect(css).toContain("--color-affirm:color-mix(insrgb,var(--accent-primary)15%,var(--bkgd-100))");
  expect(css).toContain(
    "--color-attention:color-mix(insrgb,var(--accent-secondary)15%,var(--bkgd-100))",
  );
  expect(css).toContain(
    "--color-attention-ink:color-mix(insrgb,var(--accent-secondary)70%,var(--text-primary))",
  );
});

test("the bundled faces are Inter for the chrome and Roboto Mono for the code", () => {
  const css = builtStyles().replace(/\s+/g, "");
  expect(css).toContain('--font-sans:"Inter",system-ui,sans-serif');
  expect(/@font-face\{font-family:Inter;src:url\(\/surface\/web\/static\/assets\/Inter-[^)]+\.woff2\)/.test(css)).toBe(true);
  expect(css).toContain('--font-mono:"RobotoMono"');
  expect(/@font-face\{font-family:RobotoMono;src:url\(\/surface\/web\/static\/assets\/RobotoMono-[^)]+\.ttf\)/.test(css)).toBe(true);
});

test("one sans face carries the chrome, and no rule names a second family", () => {
  const css = builtStyles().replace(/\s+/g, "");
  expect(css).toContain("h1,h2,h3{text-wrap:balance}");
  expect(/h1,h2,h3\{[^}]*font-family/.test(css)).toBe(false);
  expect(css).not.toContain("--font-display");
  expect(css).not.toContain("Canela");
});

test("the shadcn contract carries the theme, and names nothing no component reads", () => {
  const css = readFileSync(join(import.meta.dirname, "..", "src", "theme.css"), "utf8").replace(
    /\s+/g,
    "",
  );
  // Every name here is drawn with by the portal's own components or by streamdown's.
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
  // A contract name with no consumer is a colour nothing can account for, so it is not declared.
  for (const token of ["secondary", "accent", "destructive", "success", "warning", "input"]) {
    expect(css).not.toContain(`--color-${token}:`);
  }
  expect(css).toContain("--radius:0.25rem");
  expect(css).toContain("--muted:var(--bkgd-200)");
});

test("text is smoothed and wrapped, and headings balance", () => {
  const css = builtStyles().replace(/\s+/g, "");
  expect(css).toContain("-webkit-font-smoothing:antialiased");
  expect(css).toContain("text-wrap:pretty");
  expect(css).toContain("text-wrap:balance");
});

test("a control answers the pointer, and reduced motion cuts the answer short", () => {
  const css = builtStyles().replace(/\s+/g, "");
  expect(css).toContain("active\\:scale-\\[0\\.96\\]:active{scale:.96}");
  expect(css).toContain(".bg-primary{background-color:var(--primary)}");
  expect(/@media\(prefers-reduced-motion:reduce\)\{[^}]*transition-duration:\.01ms!important/.test(css)).toBe(
    true,
  );
});

test("the field surface is drawn by the field primitives and by nothing else", () => {
  const src = join(import.meta.dirname, "..", "src");
  const drawn = readdirSync(src, { recursive: true, encoding: "utf8" })
    .filter((name) => name.endsWith(".tsx") && !name.startsWith("components/ui/"))
    .filter((name) => readFileSync(join(src, name), "utf8").includes("bg-field"));

  expect(drawn).toEqual([]);
});

test("replies read as a document and member bubbles stay bubbles", async () => {
  wire({
    "/api/chats": () => json({ chats: [CHAT_ROW] }),
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
  expect(agentSide.className).not.toContain("bg-fill");
  expect(agentSide.className).not.toContain("animate-appear");
  const mineSaid = screen.getByText("mine").closest("[data-slot=bubble-content]")!;
  expect(mineSaid.closest("[data-role=me]")!.className).toContain("self-end");
  expect(mineSaid.closest("[data-role=me]")!.className).toContain("bg-fill");
  for (const said of [agentSaid, mineSaid]) expect(said.className).toContain("wrap-anywhere");

  await userEvent.type(screen.getByLabelText("Message the app"), "go");
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

test("the wordmark is the drawn ufo mark in the top bar", async () => {
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const brand = await screen.findByRole("img", { name: "ufo" });
  expect(brand.getAttribute("class")).not.toContain("tracking");
  expect(brand.getAttribute("style")).toContain("ufo-logo.svg");
});

test("the favicons use the exact light and dark brand marks", () => {
  const page = readFileSync(join(STATIC, "index.html"), "utf8");
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
  const css = builtStyles().replace(/\s+/g, "");
  expect(css).toContain("disabled\\:cursor-not-allowed:disabled{cursor:not-allowed}");
  expect(css).toContain("user-invalid\\:border-ink:user-invalid{border-color:var(--color-ink)}");
  expect(css).toContain("user-invalid\\:border-dashed:user-invalid{");
  expect(css).not.toContain("user-invalid\\:border-attention");
});

test("a placeholder is muted rather than mistaken for a value", () => {
  // The comps draw a placeholder in the third text tone, not in dimmed ink — so it is a colour,
  // and it fades against the pane the same way in both schemes.
  expect(builtStyles().replace(/\s+/g, "")).toContain(
    ".placeholder\\:text-ink-faint::placeholder{color:var(--color-ink-faint)}",
  );
});

test("muted text is the palette's second tone, never ink held back by opacity", () => {
  const src = join(import.meta.dirname, "..", "src");
  const dimmed = readdirSync(src, { recursive: true, encoding: "utf8" })
    .filter((name) => name.endsWith(".tsx") || name.endsWith(".ts"))
    .filter((name) => /opacity-\(--opacity-muted/.test(readFileSync(join(src, name), "utf8")));

  // Opacity survives only where it states a passing condition, never where it states a text tone:
  // the comps give one secondary colour, and ink at 50%-75% lands on five different greys instead.
  expect(dimmed.sort()).toEqual(["components/ui/button.tsx", "kernel/table.tsx"]);
});
