import { readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen } from "@testing-library/react";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { Table, Td } from "@/components/ui/table";

import { AGENT, MEMBER, useStreamFake, wire } from "./harness";

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
  };
  for (const [token, system] of Object.entries(basis)) {
    expect(new RegExp(`${token}:\\s*${system}\\b`).test(css)).toBe(true);
  }
  expect(css.replace(/:\s+/g, ":")).toContain("color-scheme:light dark");
});

test("the drawer fills a narrow viewport rather than overflowing it", () => {
  expect(builtStyles().replace(/\s+/g, "")).toContain("min(520px,100vw)");
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

test("the wordmark reads as one word in the sidebar", async () => {
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} />);

  const brand = await screen.findByText("ufo");
  expect(brand.textContent).toBe("ufo");
  expect(brand.className).not.toContain("tracking");
});
