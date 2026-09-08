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
const SELF = fileURLToPath(import.meta.url);

type MarkNode = [tag: string, attributes: Record<string, string>];

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

