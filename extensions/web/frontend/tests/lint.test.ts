import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "vitest";

const ROOT = join(import.meta.dirname, "..");
const SRC = join(ROOT, "src");

type Contract = { pattern: string };
type Restyle = [string, { contracts: Contract[] }];
const config = JSON.parse(readFileSync(join(ROOT, ".oxlintrc.json"), "utf8")) as {
  overrides: { files: string[]; rules: { "shadcn/no-restyle": Restyle } }[];
};
const scope = config.overrides[0];

const componentNames = (): Set<string> => {
  const named = new Set<string>();
  for (const dir of ["components/ui", "kernel"]) {
    for (const file of readdirSync(join(SRC, dir))) {
      if (!file.endsWith(".tsx")) continue;
      const source = readFileSync(join(SRC, dir, file), "utf8");
      for (const found of source.matchAll(/^export function ([A-Z]\w*)/gm)) named.add(found[1]);
    }
  }
  return named;
};

test("every name a contract spells is a component this design system exports", () => {
  const named = componentNames();
  expect(named.size).toBeGreaterThan(50);
  const unknown = scope.rules["shadcn/no-restyle"][1].contracts.flatMap((contract) =>
    [...contract.pattern.matchAll(/[A-Z]\w*/g)]
      .map((found) => found[0])
      .filter((name) => !named.has(name))
      .map((name) => `${contract.pattern} → ${name}`),
  );
  expect(unknown).toEqual([]);
});

test("every file the contract scope names is a file it can reach", () => {
  const missing = scope.files.filter((entry) => {
    const bare = entry.replace(/\/\*\*$/, "");
    const path = join(ROOT, bare);
    if (!existsSync(path)) return true;
    return entry.endsWith("/**") && !readdirSync(path).some((file) => file.endsWith(".tsx"));
  });
  expect(missing).toEqual([]);
});
