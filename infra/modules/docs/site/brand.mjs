import { cp, mkdir } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// Nothing here is committed a second time: `public/` and the copies under `src/styles` are
// gitignored, and each file is read from the one place that owns it. The apex worker reads the same
// two mark files at plan time, and `ufo-style` holds the face.
const here = dirname(fileURLToPath(import.meta.url));
const repository = join(here, "..", "..", "..", "..");
const brand = join(repository, "assets", "brand");
const style = join(repository, "core", "src", "ufo", "runtime", "skills", "ufo-style");

await mkdir(join(here, "public"), { recursive: true });
await cp(join(brand, "ufo-mark.svg"), join(here, "public", "favicon.svg"));
await cp(join(brand, "ufo-mark-on-dark.svg"), join(here, "public", "favicon-dark.svg"));

// The one face the site sets, from the tree that already holds it for the sandbox.
await mkdir(join(here, "src", "styles", "assets", "fonts"), { recursive: true });
await cp(
  join(style, "assets", "fonts", "RobotoMono-VariableFont_wght.ttf"),
  join(here, "src", "styles", "assets", "fonts", "RobotoMono-VariableFont_wght.ttf"),
);
