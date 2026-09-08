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

/** A span the browser has just inserted runs the keyframe, so the words that animate are the words that
 *  frame added. Runs last in the rehype chain, since a `data-` attribute minted before it is stripped. */
const REMEMBERED = 16;

const GLYPHING_CHARS = 140;

/** The cell is an attribute rather than a second text node, so a reply copied out of the page is the
 *  words the agent wrote and the streamed text is character for character the settled text. */
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

/** Streamdown caches the processor it builds from a plugin list, so a plugin held per component is not
 *  the plugin that runs. */
export const arrive = arriving();
