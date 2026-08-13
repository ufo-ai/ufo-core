import type { Root } from "mdast";
import { createElement } from "react";
import remarkBreaks from "remark-breaks";

import { arrive } from "@/lib/arrive";
import { Streamdown, defaultRehypePlugins, defaultRemarkPlugins, type Components } from "streamdown";
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
      remarkPlugins={REMARK_PLUGINS}
      rehypePlugins={ARRIVING_PLUGINS}
    >
      {text}
    </Streamdown>
  );
}
