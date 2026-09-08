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
 *  `<script>`, so every html node crosses into the tree as the characters it was written as. */
function literalHtml() {
  return (tree: Root) => {
    visit(tree, "html", (node) => {
      Object.assign(node, { type: "text" });
    });
  };
}

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

/** The SVG bakes its colours in at render, so each render reads the scheme mark off the root.
 *  `initialize` is a no-op because Streamdown never calls it, and each render configures the instance. */
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

/** `javascript:` is a script the reply asked the page to run under the member's session, so a link
 *  reaches only a protocol a reader can follow. An image is drawn only from this origin. */
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

const LINKABLE = /(https?:\/\/|mailto:)[^\s<>]+/gi;

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
 *  typed. An address is the exception, drawn as the anchor a reply's link is, under the same policy. */
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

/** `harden` is left out of the chain: it answers a refused link with a `[blocked]` span in its own
 *  words, and one policy for what a reply may link to already lives in `COMPONENTS`. */
const REMARK_PLUGINS = [...Object.values(defaultRemarkPlugins), remarkBreaks, literalHtml];
const REHYPE_PLUGINS = [defaultRehypePlugins.raw, defaultRehypePlugins.sanitize];
const ARRIVING_PLUGINS = [...REHYPE_PLUGINS, arrive];

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
