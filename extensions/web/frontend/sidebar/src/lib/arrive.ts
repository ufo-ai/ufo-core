import type { Root } from "hast";
import { visit } from "unist-util-visit";

import { cipherOf } from "@/lib/braille";

const WORD = /\s*\S+\s*/g;

const UNBROKEN = new Set(["code", "pre"]);

function reads(tree: Root): string {
  let text = "";
  visit(tree, "text", (node) => {
    text += node.value;
  });
  return text;
}

/** Wrap every word of a streaming reply in a span, so a word that has just arrived can fade in and
 *  a word already read cannot. Nothing here animates: a span the browser has just inserted runs the
 *  `arrive` keyframe, and React reuses the spans already on the page, so the words that animate on
 *  a frame are exactly the words that frame added. A tracked count of what is new would be a second
 *  answer to that, arriving a frame later than the reconciler's.
 *
 *  What the reconciler cannot say is where a word stands in the run that just landed, and a chunk
 *  fading in at once reads as a paragraph blinking rather than as writing. So the words of one
 *  frame are numbered — `--arrive`, which the theme turns into that word's delay — from the point
 *  the block reached on the frame before. The renderer parses one block at a time, so the point is
 *  per block, and a block is known by what it was last read as: the run that continues one of those
 *  texts is that block, grown by the words past its end.
 *
 *  A number a word already carries may fall but never rise. A delay pushed further out puts a word
 *  that has finished arriving back before its own start, and the browser plays it again — which is
 *  a settled paragraph fading in a second time while a later one is still being written. Keeping
 *  each block's own baseline is what holds that: a block re-read unchanged settles whole, and only
 *  a block that has never been read numbers every word it holds.
 *
 *  Runs last in the rehype chain, after sanitising, because a `data-` attribute minted before it
 *  would be stripped. Code keeps its own text: a fence arrives a line at a time and a word is not
 *  what it is read in. */
const REMEMBERED = 16;

/** How far back from the end of a reply a word still carries its cells. The head has to keep them
 *  for longer than one frame — the next chunk lands before a crossfade is over, and a word rewritten
 *  as plain letters mid-fade would snap. Past this the letters stand on their own, so a long reply
 *  is prose rather than a span per character, and a word leaving the window has finished with its
 *  cells already, which is why the swap cannot be seen. */
const GLYPHING_CHARS = 140;

/** A word that has just landed carries, per character, the cell that hides it. The theme draws that
 *  cell over the letter and crosses the two over one character at a time, so the head of a reply
 *  resolves out of braille the way a status line does — and, like the fade beside it, it is the
 *  browser inserting the element that runs it, never a timer.
 *
 *  The cell is an attribute rather than a second text node, so it is the theme that says it and not
 *  the document: a reply copied out of the page is the words the agent wrote, a reader hears them
 *  once, and the streamed text is character for character the settled text. Prose is not monospace,
 *  so the letter holds the box on its own and the cell is laid over the middle of it — a paragraph
 *  does not reflow under its own arrival.
 *
 *  A space is left as it is: there is nothing to hide, and a cell over one would open a gap that
 *  closes again. */
function glyphing(value: string) {
  return Array.from(value, (character, at) =>
    character.trim() === ""
      ? { type: "text" as const, value: character }
      : {
          type: "element" as const,
          tagName: "span",
          properties: {
            dataGlyph: cipherOf(character),
            style: "--cell:" + at,
          },
          children: [{ type: "text" as const, value: character }],
        },
  );
}

function arriving() {
  const read: string[] = [];
  return () => (tree: Root) => {
    const text = reads(tree);
    const before = read.reduce((longest, seen) => {
      if (!text.startsWith(seen) || seen.length < longest.length) return longest;
      return seen;
    }, "");
    read.splice(0, read.length, ...read.filter((seen) => seen !== before), text);
    if (read.length > REMEMBERED) read.shift();
    const settled = before.length;
    const glyphFrom = text.length - GLYPHING_CHARS;
    let counted = 0;
    let landing = 0;
    visit(tree, "text", (node, index, parent) => {
      if (parent === undefined || index === undefined) return;
      const start = counted;
      counted += node.value.length;
      if (parent.type === "element" && UNBROKEN.has(parent.tagName)) return;
      const words = node.value.match(WORD);
      if (words === null) return;
      let offset = start;
      const spans = words.map((value) => {
        const arrive = offset >= settled ? landing++ : 0;
        const glyphs = offset >= glyphFrom;
        offset += value.length;
        return {
          type: "element" as const,
          tagName: "span",
          properties: { dataArrive: "", style: "--arrive:" + arrive },
          children: glyphs ? glyphing(value) : [{ type: "text" as const, value }],
        };
      });
      parent.children.splice(index, 1, ...spans);
      return index + spans.length;
    });
  };
}

/** One arrival for the page. Streamdown caches the processor it builds from a plugin list, so a
 *  plugin held per component is not the plugin that runs; what a stagger has to remember therefore
 *  lives beside the plugin itself. One live reply is drawn at a time, which is its whole audience. */
export const arrive = arriving();
