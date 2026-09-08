import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const KIT = join(HERE, "src/apps/kit.ts");
const SKILL = resolve(HERE, "../../../core/src/ufo/runtime/skills/ufo-style/references/kit.md");

const kit = readFileSync(KIT, "utf8");

const owned = new Map();
const named = new Set();
for (const found of kit.matchAll(/import\s*(type\s*)?\{([^}]*)\}\s*from\s*"([^"]+)"/g)) {
  const [, typeOnly, names, from] = found;
  for (const raw of names.split(",")) {
    const entry = raw.trim();
    if (!entry) continue;
    const name = entry.replace(/^type\s+/, "").split(/\s+as\s+/).pop();
    if (typeOnly || /^type\s/.test(entry) || !from.startsWith("@/")) named.add(name);
    else owned.set(name, from.replace("@/", "src/"));
  }
}
for (const found of kit.matchAll(/import \* as (\w+) from/g)) named.add(found[1]);

const block = kit.slice(kit.indexOf("export {"), kit.length);
const exported = [...block.matchAll(/^\s{2}([A-Za-z][A-Za-z0-9]*),$/gm)].map((m) => m[1]);

const source = new Map();
const read = (module) => {
  if (!source.has(module)) {
    for (const ext of [".tsx", ".ts"]) {
      try {
        source.set(module, readFileSync(join(HERE, module + ext), "utf8"));
        break;
      } catch {}
    }
  }
  return source.get(module) ?? "";
};

/** Only whitespace may separate the two: a docstring with a type or a constant between it and the
 *  definition is documenting that, and the definition is undocumented. */
const says = (module, name) => {
  const text = read(module);
  const at = text.search(new RegExp(`^export (?:async function|function|const) ${name}\\b`, "m"));
  if (at < 0) return null;
  const before = text.slice(0, at);
  const close = before.trimEnd();
  if (!close.endsWith("*/")) return null;
  const open = close.lastIndexOf("/**");
  if (open < 0) return null;
  const body = close.slice(open + 3, -2);
  const flat = body.replace(/^\s*\*\s?/gm, " ").replace(/\s+/g, " ").trim();
  const stop = flat.search(/[.:](?:\s|$)/);
  return (stop < 0 ? flat : flat.slice(0, stop + 1)).trim();
};

const rows = [];
const undescribed = [];
const rest = [];
for (const name of exported) {
  if (named.has(name)) {
    rest.push(name);
    continue;
  }
  const module = owned.get(name);
  const said = module ? says(module, name) : null;
  if (said) rows.push({ name, module, said });
  else undescribed.push(`${name} (${module ?? "not imported"})`);
}
if (undescribed.length) {
  console.error(
    `catalogue: ${undescribed.length} kit values carry no docstring directly over their definition, ` +
      "so the page that tells an agent what the kit holds would leave them out:\n  " +
      undescribed.join("\n  "),
  );
  process.exit(1);
}

const families = new Map();
for (const row of rows) {
  const file = row.module.split("/").pop();
  if (!families.has(file)) families.set(file, []);
  families.get(file).push(row);
}

const lines = [
  "# The kit",
  "",
  "Every component `ufo/kit` publishes, with what it is in the words of the file that defines it.",
  "Generated from `kit.ts` at build time — a name here is a name the kit exports, and a name absent",
  "here is not in the kit whatever it is called elsewhere.",
  "",
  "Reach for one of these before composing a shape out of `div`s: a page built from them answers to",
  "the theme, both colour schemes and every width at once, and a shape built beside them does not.",
  "",
];
for (const [file, members] of [...families].sort()) {
  lines.push(`## ${file}`, "");
  for (const { name, said } of members) lines.push(`- **\`${name}\`** — ${said}`);
  lines.push("");
}

// Naming every export is what lets the gate be a set equality, so a component added to the kit and
// never regenerated here fails a check instead of being invisible to the agent that would have used it.
lines.push(
  "## Also published",
  "",
  "Named without description — the types, and the React and Tabler exports a page imports from the",
  "kit rather than from a global, which a framed page does not have.",
  "",
  rest.map((name) => `\`${name}\``).join(", ") + ".",
  "",
);

mkdirSync(dirname(SKILL), { recursive: true });
writeFileSync(SKILL, lines.join("\n"));
console.log(`catalogue: ${exported.length} exports, ${rows.length} described`);
