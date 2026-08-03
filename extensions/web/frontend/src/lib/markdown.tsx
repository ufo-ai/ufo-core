import DOMPurify from "dompurify";
import { Marked } from "marked";
import { memo } from "react";

export const SETTLED_MIN_CHARS = 2048;
export const FENCE_SPLIT_CHARS = 16_384;

export type StreamSegment = { text: string; parsePrefix: string };
export type StreamSplit = { settled: StreamSegment[]; tail: StreamSegment };

const FENCE_OPEN = /^ {0,3}(`{3,}|~{3,})/;
const MARKER_SHAPED = /^ {0,3}[`~]/;
const CONTINUATION = /^(\d+[.)]\s|[-*+]\s|>)/;

type Fence = { char: string; size: number } | null;

export function splitSettled(text: string): StreamSplit {
  const lines = text.split(/(?<=\n)/);
  const settled: StreamSegment[] = [];
  let pending = "";
  let pendingPrefix = "";
  let fence: Fence = null;
  const cutHard = () => {
    if (fence === null) return;
    while (pending.length > FENCE_SPLIT_CHARS) {
      let cut = FENCE_SPLIT_CHARS;
      if (pending[cut - 1] === "\n") {
        const nextEnd = pending.indexOf("\n", cut);
        if (nextEnd === -1) {
          if (MARKER_SHAPED.test(pending.slice(cut))) return;
        } else if (MARKER_SHAPED.test(pending.slice(cut, nextEnd + 1))) {
          cut = nextEnd + 1;
        }
      }
      if (cut >= pending.length) return;
      settled.push({ text: pending.slice(0, cut), parsePrefix: pendingPrefix });
      pending = pending.slice(cut);
      pendingPrefix = fence.char.repeat(fence.size) + "\n";
    }
  };
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const terminated = line.endsWith("\n");
    pending += line;
    const bare = terminated ? line.slice(0, -1) : line;
    if (!terminated) {
      if (fence !== null && !MARKER_SHAPED.test(line)) cutHard();
      continue;
    }
    const marker = bare.match(FENCE_OPEN);
    if (marker) {
      const char = marker[1][0];
      const size = marker[1].length;
      if (fence === null) fence = { char, size };
      else if (fence.char === char && size >= fence.size) fence = null;
      else cutHard();
      continue;
    }
    if (fence !== null) {
      cutHard();
      continue;
    }
    if (bare.trim() !== "") continue;
    if (pending.length < SETTLED_MIN_CHARS) continue;
    if (!settlesAfter(lines, index)) continue;
    settled.push({ text: pending, parsePrefix: pendingPrefix });
    pending = "";
    pendingPrefix = "";
  }
  return { settled, tail: { text: pending, parsePrefix: pendingPrefix } };
}

function settlesAfter(lines: string[], index: number): boolean {
  for (let next = index + 1; next < lines.length; next += 1) {
    const line = lines[next];
    if (!line.endsWith("\n")) return false;
    const bare = line.slice(0, -1);
    if (bare.trim() === "") continue;
    return !/^\s/.test(bare) && !CONTINUATION.test(bare);
  }
  return false;
}

const ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
};

function escapeHtml(text: string): string {
  return text.replace(/[&<>]/g, (ch) => ESCAPES[ch]);
}

const MARKED = new Marked({
  gfm: true,
  breaks: true,
  renderer: {
    html(token: { text: string }) {
      return escapeHtml(token.text);
    },
    text(token: { type: string; text: string; escaped?: boolean; tokens?: unknown[] }) {
      if (token.tokens) return false;
      if (token.type === "text" && token.escaped) return escapeHtml(token.text);
      return false;
    },
  },
});

const ALLOWED_TAGS = [
  "a",
  "blockquote",
  "br",
  "code",
  "del",
  "em",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "hr",
  "img",
  "input",
  "li",
  "ol",
  "p",
  "pre",
  "strong",
  "table",
  "tbody",
  "td",
  "th",
  "thead",
  "tr",
  "ul",
];

const ALLOWED_ATTR = ["alt", "checked", "disabled", "href", "rel", "src", "start", "target", "type"];

function sameOriginSrc(src: string | null): boolean {
  if (!src) return false;
  try {
    return new URL(src, location.href).origin === location.origin;
  } catch {
    return false;
  }
}

DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
  if (node.tagName === "IMG" && !sameOriginSrc(node.getAttribute("src"))) {
    node.remove();
  }
  if (node.tagName === "INPUT") {
    if (node.getAttribute("type") !== "checkbox") {
      node.remove();
      return;
    }
    node.setAttribute("disabled", "");
  }
});

export function renderMarkdown(text: string): string {
  let parsed: string;
  try {
    parsed = MARKED.parse(text, { async: false }) as string;
  } catch {
    parsed = "<pre>" + escapeHtml(text) + "</pre>";
  }
  return DOMPurify.sanitize(parsed, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ALLOW_DATA_ATTR: false,
  });
}

const PROSE = [
  "[&>*:first-child]:mt-0 [&>*:last-child]:mb-0",
  "[&_p]:my-sm",
  "[&_:is(h1,h2,h3,h4,h5,h6)]:mt-lg [&_:is(h1,h2,h3,h4,h5,h6)]:mb-sm [&_:is(h1,h2,h3,h4,h5,h6)]:font-strong",
  "[&_h1]:text-title [&_:is(h2,h3,h4,h5,h6)]:text-body",
  "[&_:is(ul,ol)]:my-sm [&_:is(ul,ol)]:pl-4xl [&_li]:my-hair",
  "[&_code]:font-mono [&_code]:text-label [&_code]:bg-fill-subtle [&_code]:rounded-sm [&_code]:px-2xs",
  "[&_pre]:my-sm [&_pre]:overflow-x-auto [&_pre]:rounded-panel [&_pre]:bg-fill-subtle [&_pre]:p-lg",
  "[&_pre_code]:bg-transparent [&_pre_code]:p-0",
  "[&_blockquote]:my-sm [&_blockquote]:border-l-2 [&_blockquote]:border-edge [&_blockquote]:pl-lg [&_blockquote]:opacity-(--muted-soft)",
  "[&_table]:my-sm [&_table]:block [&_table]:max-w-full [&_table]:overflow-x-auto [&_table]:border-collapse",
  "[&_:is(th,td)]:border [&_:is(th,td)]:border-edge-soft [&_:is(th,td)]:px-sm [&_:is(th,td)]:py-2xs [&_th]:text-left",
  "[&_hr]:my-lg [&_hr]:border-edge-soft",
  "[&_a]:text-link [&_a]:underline",
  "[&_img]:my-sm [&_img]:max-w-full [&_img]:rounded-panel",
  "[&_li>input]:mr-xs [&_li>input]:align-middle",
].join(" ");

export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return <div className={PROSE} dangerouslySetInnerHTML={{ __html: renderMarkdown(text) }} />;
});

export function StreamingBody({ text }: { text: string }) {
  const { settled, tail } = splitSettled(text);
  return (
    <>
      {settled.map((segment, index) => (
        <Markdown key={index} text={segment.parsePrefix + segment.text} />
      ))}
      {tail.text ? <Markdown text={tail.parsePrefix + tail.text} /> : null}
    </>
  );
}
