import { cp, mkdir } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// Nothing here is committed a second time: `public/` and the copies under `src/styles` are
// gitignored, and each file is read from the one place that owns it. The apex worker reads the same
// two mark files at plan time, and `ufo-style` holds the face.
const here = dirname(fileURLToPath(import.meta.url));
const repository = join(here, "..", "..");
const brand = join(repository, "assets", "brand");
const style = join(repository, "core", "src", "ufo", "runtime", "skills", "ufo-style");

await mkdir(join(here, "public"), { recursive: true });
await cp(join(brand, "ufo-mark.svg"), join(here, "public", "favicon.svg"));
await cp(join(brand, "ufo-mark-on-dark.svg"), join(here, "public", "favicon-dark.svg"));

// The header draws the brand's own lockup rather than setting `∵ UFO` as text: Roboto Mono carries
// no glyph for U+2235, and the lockup holds the spacing the logo sheet specifies.
await mkdir(join(here, "src", "assets"), { recursive: true });
await cp(join(brand, "ufo-lockup.svg"), join(here, "src", "assets", "lockup.svg"));
await cp(join(brand, "ufo-lockup-on-dark.svg"), join(here, "src", "assets", "lockup-on-dark.svg"));

// The two faces the site sets, from the tree that already holds them for the sandbox.
await mkdir(join(here, "src", "styles", "assets", "fonts"), { recursive: true });
for (const face of ["Inter-VariableFont_wght.woff2", "RobotoMono-VariableFont_wght.ttf"]) {
  await cp(
    join(style, "assets", "fonts", face),
    join(here, "src", "styles", "assets", "fonts", face),
  );
}
