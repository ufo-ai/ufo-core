import type { Root } from "mdast";
import type { MermaidConfig } from "mermaid";
import { createElement, type ReactNode } from "react";
import remarkBreaks from "remark-breaks";

import { arrive } from "@/lib/arrive";
import {
  Streamdown,
  defaultRehypePlugins,
  defaultRemarkPlugins,
  type Components,
  type DiagramPlugin,
  type MermaidErrorComponentProps,
} from "streamdown";
import { visit } from "unist-util-visit";

/** Raw HTML an agent wrote is prose, not markup: a reply that says `<script>` is talking about
 *  `<script>`. Parsed, the sentence would lose the words it needs, so every html node crosses into
 *  the tree as the characters it was written as, before it can become an element. */
function literalHtml() {
  return (tree: Root) => {
    visit(tree, "html", (node) => {
      Object.assign(node, { type: "text" });
    });
  };
}

/** Streamdown classes its prose for a reading width it does not know: `list-inside` puts a wrapped
 *  line under its own bullet, a quotation arrives italic and behind a four-pixel rule. Those
 *  elements are drawn bare and `typeset` sets them, which is the register every other document in
 *  the portal is read in. What stays Streamdown's is what it draws better than an element can — the
 *  code block with its language and the table with its own scroll — now that both resolve their
 *  colours, type and radii through this portal's tokens. */
const BARE_TAGS = [
  "p",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "ul",
  "ol",
  "li",
  "blockquote",
  "hr",
  "strong",
  "em",
  "del",
] as const;

function bare(tag: (typeof BARE_TAGS)[number]) {
  return ({ node: _node, className: _className, ...props }: Record<string, unknown>) =>
    createElement(tag, props);
}

/** A `mermaid` fence is a drawing the reply describes, so it is drawn. The library that draws it
 *  is loaded the first time a diagram asks for it, never in the page's own bundle. The SVG bakes
 *  its colours in at render, so each render reads the scheme mark `scheme.ts` keeps on the root —
 *  greys on a light page, mermaid's dark set on a dark one. A chart that will not parse —
 *  half-streamed, or simply wrong — shows the fence's own text as the code it is; `initialize` is
 *  a no-op because Streamdown never calls it, and each render configures the instance it
 *  awaited. */
const MERMAID_CONFIG: MermaidConfig = {
  startOnLoad: false,
  suppressErrorRendering: true,
};

function paintedTheme(): "dark" | "neutral" {
  const root = document.documentElement.classList;
  if (root.contains("dark")) return "dark";
  if (root.contains("light")) return "neutral";
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "neutral";
}

const MERMAID_PLUGINS: { mermaid: DiagramPlugin } = {
  mermaid: {
    name: "mermaid",
    type: "diagram",
    language: "mermaid",
    getMermaid: (config = MERMAID_CONFIG) => ({
      initialize: () => {},
      render: async (id, source) => {
        const { default: mermaid } = await import("mermaid");
        mermaid.initialize({ ...config, theme: paintedTheme() });
        return mermaid.render(id, source);
      },
    }),
  },
};

function MermaidSource({ chart }: MermaidErrorComponentProps) {
  return (
    <pre>
      <code>{chart}</code>
    </pre>
  );
}

const MERMAID_OPTIONS = { config: MERMAID_CONFIG, errorComponent: MermaidSource };

const SPEAKABLE_PROTOCOLS = ["http:", "https:", "mailto:"];

function resolved(url: string): URL | null {
  try {
    return new URL(url, location.href);
  } catch {
    return null;
  }
}

/** A link the member did not write opens in its own tab, carries no opener, and reaches only a
 *  protocol a reader can follow — `javascript:` is a script the reply asked the page to run under
 *  the member's session, and it stays the words it was written as. An image is drawn only from this
 *  origin, so a reply cannot report who read it to a third party. The checkbox markdown mints for a
 *  task list is a mark on the page, never a control. */
const COMPONENTS: Components = {
  ...Object.fromEntries(BARE_TAGS.map((tag) => [tag, bare(tag)])),
  a: ({ node: _node, className: _className, href, children, ...props }) => {
    const target = typeof href === "string" && href !== "" ? resolved(href) : null;
    if (!target || !SPEAKABLE_PROTOCOLS.includes(target.protocol)) return <>{children}</>;
    return (
      <a {...props} href={href} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    );
  },
  img: ({ node: _node, className: _className, src, ...props }) => {
    const source = typeof src === "string" ? resolved(src) : null;
    return source && source.origin === location.origin ? <img {...props} src={src} /> : null;
  },
  input: ({ node: _node, className: _className, ...props }) =>
    props.type === "checkbox" ? <input {...props} disabled /> : null,
};

/** Only an address written with the scheme it is followed by: anything else a member typed reads as
 *  the words it is, so `www.example.com` is never resolved against the page it was typed on. */
const LINKABLE = /(https?:\/\/|mailto:)[^\s<>]+/gi;

/** An address typed into a sentence ends before the sentence does: the full stop that closes the
 *  sentence, and a bracket the sentence put around the address, belong to the sentence. The scheme
 *  is never trimmed into, so what is left still resolves as the address it was written as. */
function address(match: string, scheme: string): string {
  let opened = 0;
  let closed = 0;
  for (const character of match) {
    if (character === "(") opened += 1;
    else if (character === ")") closed += 1;
  }
  let end = match.length;
  while (end > scheme.length) {
    const last = match[end - 1];
    const closing = last === ")" && closed > opened;
    if (!".,:;!?'\"".includes(last) && !closing) break;
    if (closing) closed -= 1;
    end -= 1;
  }
  return match.slice(0, end);
}

/** A member's own words are not markup: their `#`, `*` and `[docs](url)` stay the characters they
 *  typed. An address is the one exception, because a member who types one means the place it points
 *  at, and it is drawn as the anchor a reply's link is drawn as, under the same policy. */
export function Linked({ text }: { text: string }) {
  const parts: ReactNode[] = [];
  let read = 0;
  for (const match of text.matchAll(LINKABLE)) {
    const url = address(match[0], match[1]);
    const target = resolved(url);
    if (!target || !SPEAKABLE_PROTOCOLS.includes(target.protocol)) continue;
    parts.push(text.slice(read, match.index));
    parts.push(
      <a key={match.index} href={url} target="_blank" rel="noopener noreferrer">
        {url}
      </a>,
    );
    read = match.index + url.length;
  }
  if (!parts.length) return <>{text}</>;
  parts.push(text.slice(read));
  return <>{parts}</>;
}

/** A single newline is a line break, because an agent writing a list of lines means the lines it
 *  wrote. `harden` is left out of the chain: it answers a refused link with a `[blocked]` span in
 *  its own words, and one policy for what a reply may link to and load already lives in
 *  `COMPONENTS`. */
const REMARK_PLUGINS = [...Object.values(defaultRemarkPlugins), remarkBreaks, literalHtml];
const REHYPE_PLUGINS = [defaultRehypePlugins.raw, defaultRehypePlugins.sanitize];
const ARRIVING_PLUGINS = [...REHYPE_PLUGINS, arrive];

/** A settled document: what it holds is all it will ever hold, so no block is completed for it. */
export function Markdown({ text }: { text: string }) {
  return (
    <Streamdown
      className="typeset"
      mode="static"
      controls={false}
      components={COMPONENTS}
      plugins={MERMAID_PLUGINS}
      mermaid={MERMAID_OPTIONS}
      remarkPlugins={REMARK_PLUGINS}
      rehypePlugins={REHYPE_PLUGINS}
    >
      {text}
    </Streamdown>
  );
}

/** A reply arriving a token at a time. The text is split into blocks and each is memoised, so a
 *  settled paragraph is not re-parsed on every frame, and the block still being written is
 *  completed as it goes — a half-typed fence reads as the code block it is becoming rather than as
 *  three backticks. */
export function StreamingBody({ text }: { text: string }) {
  return (
    <Streamdown
      className="typeset"
      controls={false}
      components={COMPONENTS}
      plugins={MERMAID_PLUGINS}
      mermaid={MERMAID_OPTIONS}
      remarkPlugins={REMARK_PLUGINS}
      rehypePlugins={ARRIVING_PLUGINS}
    >
      {text}
    </Streamdown>
  );
}
