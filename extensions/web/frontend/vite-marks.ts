import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import type { Plugin } from "vite";

const OUTLINE_NODES = new URL(
  "./node_modules/@tabler/icons/tabler-nodes-outline.json",
  import.meta.url,
);
const MARK_LETTER = /^[a-z]$/;
const MARK_ATTRIBUTES = new Set(["d", "fill", "stroke", "opacity"]);
const SPRITES = "__MARK_SPRITES__";
/** This file, which spells that name rather than reading it: a suite importing the cut reaches
 *  this module through the same pipeline, where a substitution would land inside the string. */
const SELF = fileURLToPath(import.meta.url);

type MarkNode = [tag: string, attributes: Record<string, string>];

/**
 * Every outline mark tabler draws, cut into one sprite per opening letter and named by that
 * sprite's content hash. The bundle carries the marks the picker offers and reads any other slug
 * from its letter's sprite, so the set an app's mark may come from is the whole library rather
 * than the few names a bundled import list can hold. The sprites land in `assets/`, which is what
 * the surface serves and publishes to the shared store, and the hash in the name is what lets a
 * pod answer another build's page from that store.
 *
 * A tag or an attribute this cut has not seen raises: `agentIcon.tsx` draws paths and exactly
 * these four attributes, and a fifth arriving with a tabler release would otherwise be dropped
 * from the mark without a word.
 */
export function markSprites(): Map<string, { file: string; body: string }> {
  const outline: Record<string, MarkNode[]> = JSON.parse(readFileSync(OUTLINE_NODES, "utf8"));
  const symbols = new Map<string, string[]>();
  for (const [slug, nodes] of Object.entries(outline)) {
    const letter = slug.slice(0, 1);
    if (!MARK_LETTER.test(letter)) throw new Error(`tabler names a mark ${slug}`);
    const paths = nodes.map(([tag, attributes]) => {
      if (tag !== "path") throw new Error(`tabler draws ${slug} with a ${tag}`);
      const written = Object.entries(attributes).map(([name, value]) => {
        if (!MARK_ATTRIBUTES.has(name)) throw new Error(`tabler draws ${slug} with ${name}`);
        return `${name}="${value}"`;
      });
      return `<path ${written.join(" ")}/>`;
    });
    const held = symbols.get(letter) ?? [];
    held.push(`<symbol id="${slug}" viewBox="0 0 24 24">${paths.join("")}</symbol>`);
    symbols.set(letter, held);
  }
  return new Map(
    [...symbols].map(([letter, held]) => {
      const body = `<svg xmlns="http://www.w3.org/2000/svg">${held.join("")}</svg>`;
      const hash = createHash("sha256").update(body).digest("hex").slice(0, 8);
      return [letter, { file: `assets/tabler-${letter}-${hash}.svg`, body }];
    }),
  );
}

/**
 * The sprites, and the URL the module that draws a mark reads each one from. `__MARK_SPRITES__`
 * carries those URLs, and a build writes them as `import.meta.ROLLUP_FILE_URL_…` — the reference
 * rollup keeps for a file it emitted, rendered as `new URL("assets/tabler-…", import.meta.url)`.
 * That is a reference the next bundler follows: the vite a member runs over the SDK in their own
 * sandbox emits the sprite into their page's own tree and rewrites the name, which is what a URL
 * assembled from pieces at runtime can never make it do.
 *
 * The dev server has no bundle to emit into and answers the same names from memory, so the loop
 * that never builds the tree draws every mark the built page does. The emit is a build's alone —
 * `emitFile` exists under neither `vite dev` nor `vitest`.
 */
export function tablerMarks(): Plugin[] {
  const sprites = markSprites();
  const served = new Map([...sprites.values()].map(({ file, body }) => [file, body]));
  const emitted = new Map<string, string>();
  let base = "/";
  return [
    {
      name: "tabler-marks-emit",
      apply: "build",
      buildStart() {
        for (const [letter, { file, body }] of sprites) {
          emitted.set(letter, this.emitFile({ type: "asset", fileName: file, source: body }));
        }
      },
    },
    {
      name: "tabler-marks",
      configResolved(config) {
        base = config.base;
      },
      transform(code, id) {
        if (id.startsWith(SELF) || !code.includes(SPRITES)) return null;
        const read = [...sprites].map(([letter, { file }]) => {
          const reference = emitted.get(letter);
          const url = reference
            ? `import.meta.ROLLUP_FILE_URL_${reference}`
            : JSON.stringify(base + file);
          return `${letter}:${url}`;
        });
        return { code: code.replaceAll(SPRITES, `{${read.join(",")}}`), map: { mappings: "" } };
      },
      configureServer(server) {
        server.middlewares.use((request, response, next) => {
          const asked = (request.url ?? "").split("?")[0].replace(base, "").replace(/^\//, "");
          const body = served.get(asked);
          if (body === undefined) return next();
          response.setHeader("content-type", "image/svg+xml");
          response.end(body);
        });
      },
    },
  ];
}

