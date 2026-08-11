import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { Table, Td } from "@/components/ui/table";
import { Notice } from "@/kernel/panel";
import { TurnLine } from "@/views/Conversations";

import { CHAT_ROW, CONVO_ID, AGENT, MEMBER, StreamFake, TURN_ID, json, useStreamFake, wire } from "./harness";

const STATIC = join(import.meta.dirname, "..", "..", "ufo_ext_web", "static");

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

const authoredColours = (css: string) =>
  css.replace(PROBES, "").replace(HUELESS, "").match(AUTHORED) ?? [];

beforeEach(() => {
  location.hash = "";
  useStreamFake();
});

test("every colour the portal paints resolves through the system-colour tokens", () => {
  const css = builtStyles();

  expect(authoredColours(css)).toEqual([]);
  const basis = {
    "--color-surface": "Canvas",
    "--color-ink": "CanvasText",
    "--color-field": "Field",
    "--color-field-ink": "FieldText",
    "--color-link": "LinkText",
  };
  for (const [token, system] of Object.entries(basis)) {
    expect(new RegExp(`${token}:\\s*${system}\\b`).test(css)).toBe(true);
  }
  expect(css.replace(/:\s+/g, ":")).toContain("color-scheme:light dark");
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
  expect(css).toContain("h2{font-size:var(--text-subtitle)");
  expect(css).toContain("font-size:var(--text-ui)");
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
    <TurnLine
      turn={{
        id: TURN_ID,
        agent_id: AGENT.id,
        conversation_id: CONVO_ID,
        seq: 1,
        status: "done",
        created_at: "2026-08-06T09:00:00Z",
        inbound: blob,
        outcome: blob,
        error_class: null,
        subagent_profile: "code_review",
        parent_turn_id: null,
      }}
      depth={0}
    />,
  );

  for (const bubble of screen.getAllByText(blob)) {
    expect(bubble.className).toContain("wrap-anywhere");
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
  expect(css).toContain("--shadow-raised:");
  expect(css).toContain("box-shadow:var(--shadow-raised)");
  expect(css).toContain("--color-link:LinkText");
  expect(css).toContain("--color-attention:Mark");
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
  expect(css).toContain("--color-ink-hover:color-mix(insrgb,CanvasText88%,transparent)");
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
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const agentSide = (await screen.findByText("reply")).closest("[data-role=agent]")!;
  expect(agentSide.className).toContain("leading-reading");
  expect(agentSide.className).toContain("w-full");
  expect(agentSide.className).not.toContain("bg-fill");
  expect(agentSide.className).not.toContain("animate-appear");
  const mineSide = screen.getByText("mine").closest("[data-role=me]")!;
  expect(mineSide.className).toContain("self-end");
  expect(mineSide.className).toContain("bg-fill");
  for (const side of [agentSide, mineSide]) expect(side.className).toContain("wrap-anywhere");

  await userEvent.type(screen.getByLabelText("Message the agent"), "go");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("message", { text: "streaming now" });
  const live = (await screen.findByText("streaming now")).closest("[data-role=agent]")!;
  expect(live.className).toContain("animate-appear");
  StreamFake.last().emit("terminal", { status: "done", model: "opus", tokens: 1, cost_micro_usd: 0 });
  await waitFor(() => {
    const settled = screen.getByText("streaming now").closest("[data-role=agent]")!;
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

test("the wordmark reads as one word in the sidebar", async () => {
  wire({});
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const brand = await screen.findByText("ufo");
  expect(brand.textContent).toBe("ufo");
  expect(brand.className).not.toContain("tracking");
});

test("a field shows the member that it is disabled, or that they left it invalid", () => {
  const css = builtStyles().replace(/\s+/g, "");
  expect(css).toContain("disabled\\:cursor-not-allowed:disabled{cursor:not-allowed}");
  expect(css).toContain("user-invalid\\:border-ink:user-invalid{border-color:var(--color-ink)}");
  expect(css).toContain("user-invalid\\:border-dashed:user-invalid{");
  expect(css).not.toContain("user-invalid\\:border-attention");
});

test("a placeholder is muted rather than mistaken for a value", () => {
  expect(builtStyles().replace(/\s+/g, "")).toContain(
    ".placeholder\\:opacity-\\(--muted\\)::placeholder{opacity:var(--muted)}",
  );
});
