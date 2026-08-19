import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig, type Plugin } from "vite";

import { devLocalPath } from "./dev-routing";

const BASE = "/surface/web/static/";
const OUTLINE_NODES = new URL(
  "./node_modules/@tabler/icons/tabler-nodes-outline.json",
  import.meta.url,
);
const MARK_LETTER = /^[a-z]$/;
const MARK_ATTRIBUTES = new Set(["d", "fill", "stroke", "opacity"]);

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
function markSprites(): Map<string, { file: string; body: string }> {
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
 * The sprites, and the one name each is served under. `__MARK_SPRITES__` is how the module that
 * draws a mark learns those hashed names; the dev server answers them from memory, so the loop
 * that never builds the tree draws every mark the built page does. The emit is a build's alone —
 * `define` and the dev answer are wanted under both commands, and `emitFile` exists under neither
 * `vite dev` nor `vitest`.
 */
function tablerMarks(): Plugin[] {
  const sprites = markSprites();
  const served = new Map([...sprites.values()].map(({ file, body }) => [file, body]));
  return [
    {
      name: "tabler-marks",
      config: () => ({
        define: {
          __MARK_SPRITES__: JSON.stringify(
            Object.fromEntries([...sprites].map(([letter, { file }]) => [letter, file])),
          ),
        },
      }),
      configureServer(server) {
        server.middlewares.use((request, response, next) => {
          const asked = (request.url ?? "").split("?")[0].replace(BASE, "").replace(/^\//, "");
          const body = served.get(asked);
          if (body === undefined) return next();
          response.setHeader("content-type", "image/svg+xml");
          response.end(body);
        });
      },
    },
    {
      name: "tabler-marks-emit",
      apply: "build",
      buildStart() {
        for (const { file, body } of sprites.values()) {
          this.emitFile({ type: "asset", fileName: file, source: body });
        }
      },
    },
  ];
}

export default defineConfig({
  base: BASE,
  plugins: [react(), tailwindcss(), tablerMarks()],
  resolve: {
    alias: {
      "@": new URL("./src", import.meta.url).pathname,
      // The brand's own artwork, drawn once and worn by every surface. The portal reads it where
      // the brand keeps it, so no copy of a mark can drift from the one the favicons are cut from.
      "@brand": new URL("../../../assets/brand", import.meta.url).pathname,
    },
  },
  build: {
    outDir: "../ufo_ext_web/static",
    emptyOutDir: true,
  },
  server: {
    // The boot suite imports the built entry from `../ufo_ext_web/static`, and a mark is read from
    // `assets/brand` at the tree's root; both sit above this root, which the default allow-list
    // refuses.
    fs: { allow: ["../../.."] },
    proxy: {
      // `changeOrigin` stays off because `open_session` answers a 303 rebuilt from the forwarded
      // Host: rewritten, the sign-in lands on the backend, which serves the gitignored built tree
      // this server exists to bypass. `tests/viteconfig.test.ts` holds that and the rest of this
      // rule; `dev-routing.ts` owns which requests stay here.
      "^/(surface/web|ext/)": {
        target: "http://localhost:8710",
        changeOrigin: false,
        bypass: (req) => devLocalPath(req.method ?? "GET", req.url ?? ""),
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
    // A half-hour offset with no daylight rule: a run under it proves a wall clock is converted
    // to an instant rather than passed along, which a run under UTC cannot tell apart.
    env: { TZ: "Asia/Kolkata" },
  },
});
