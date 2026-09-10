import { readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { TypographyPage } from "@/blocks/docs/TypographyPage";
import { CodeBlock, Figure, Prose } from "@/blocks/typography";

const STYLESHEET = readFileSync(join(import.meta.dirname, "..", "..", "src", "blocks", "typography.css"), "utf8");

const EXAMPLES = [
  "Document",
  "h1",
  "h2",
  "h3",
  "h4",
  "p",
  "blockquote",
  "table",
  "list",
  "Inline code",
  "Lead",
  "Large",
  "Small",
  "Muted",
  "Code block",
  "Code block plain",
  "Code block wrapped",
  "Alignment",
  "Mono body",
];

describe("Prose", () => {
  it("defaults to the capped column in the sans face", () => {
    const { container } = render(
      <Prose>
        <p>Grants are listed on the workspace page.</p>
      </Prose>,
    );
    const root = container.querySelector(".blk-prose");
    expect(root?.getAttribute("data-width")).toBe("prose");
    expect(root?.getAttribute("data-font")).toBe("sans");
    expect(root?.getAttribute("data-align")).toBe("start");
  });

  it("centres the capped column on request, and the stylesheet answers with the auto margin", () => {
    const { container } = render(
      <Prose align="center">
        <p>Grants are listed on the workspace page.</p>
      </Prose>,
    );
    expect(container.querySelector(".blk-prose")?.getAttribute("data-align")).toBe("center");
    expect(STYLESHEET).toContain('.blk-prose[data-align="center"] { margin-inline: auto; }');
  });

  it("carries the width and font it is given", () => {
    const { container } = render(
      <Prose width="full" font="mono">
        <p>The migrate job runs to completion before the fleet rolls.</p>
      </Prose>,
    );
    const root = container.querySelector(".blk-prose");
    expect(root?.getAttribute("data-width")).toBe("full");
    expect(root?.getAttribute("data-font")).toBe("mono");
  });
});

describe("CodeBlock", () => {
  it("renders the listing as code inside a pre", () => {
    const { container } = render(<CodeBlock language="tsx">{"const width = 640;"}</CodeBlock>);
    const code = container.querySelector("pre.blk-code > code");
    expect(code?.textContent).toBe("const width = 640;");
    expect(code?.className).toBe("language-tsx");
    expect(container.querySelector("pre")?.getAttribute("data-language")).toBe("tsx");
    expect(container.querySelector("pre")?.getAttribute("data-variant")).toBe("filled");
    expect(container.querySelector("pre")?.getAttribute("data-wrap")).toBeNull();
  });

  it("drops its ground and folds its lines when the page carries the listing itself", () => {
    const { container } = render(
      <CodeBlock variant="plain" wrap>
        {"const local = \"http://localhost:3000\";"}
      </CodeBlock>,
    );
    const pre = container.querySelector("pre.blk-code");
    expect(pre?.getAttribute("data-variant")).toBe("plain");
    expect(pre?.getAttribute("data-wrap")).toBe("true");
  });
});

describe("Figure", () => {
  it("renders the image and its caption", () => {
    const { container } = render(
      <Figure src="data:image/svg+xml,%3Csvg/%3E" alt="A channel thread" caption="One turn, two objects." />,
    );
    expect(screen.getByAltText("A channel thread")).toBeDefined();
    expect(container.querySelector("figcaption")?.textContent).toBe("One turn, two objects.");
  });

  it("omits the caption when none is given", () => {
    const { container } = render(<Figure src="data:image/svg+xml,%3Csvg/%3E" alt="A channel thread" />);
    expect(container.querySelector("figcaption")).toBeNull();
  });
});

describe("TypographyPage", () => {
  it("shows every example", () => {
    render(<TypographyPage />);
    for (const name of EXAMPLES) {
      expect(screen.getByRole("heading", { level: 3, name })).toBeDefined();
    }
  });

  it("shows the source behind the Code tab", async () => {
    render(<TypographyPage />);
    const example = screen.getByRole("heading", { level: 3, name: "Document" }).parentElement;
    if (!example) throw new Error("the Document example has no container");
    await userEvent.click(within(example).getByRole("tab", { name: "Code" }));
    expect(within(example).getByText(/Prose/)).toBeDefined();
  });
});
