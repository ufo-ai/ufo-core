import { Fragment, useState, type ComponentType, type ReactNode } from "react";

import { DocSection } from "@/blocks/docs/Docs";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemMedia,
  ItemMeta,
  ItemTitle,
  StatusIcon,
  Tag,
} from "@/blocks/item";
import { CodeBlock, Prose } from "@/blocks/typography";
import runsFile from "@/blocks/recipes/runs.json";
import "@/blocks/docs/recipes.css";

type Check = { line: string; pass: boolean };

type Run = {
  recipe: string;
  round: number;
  agent: string;
  verdict: "pass" | "fail" | "pending";
  notes: string[];
  at: string;
  width?: string;
  checks?: Check[];
};

type Width = "lane" | "column" | "wide" | "fill";

type Block =
  | { kind: "paragraph"; text: string }
  | { kind: "code"; text: string }
  | { kind: "list"; items: string[] };

type Section = { title: string; blocks: Block[] };

type Recipe = {
  name: string;
  title: string;
  intent: string;
  sections: Section[];
  data: Record<string, unknown>;
  build?: ComponentType<{ data: unknown }>;
};

const SOURCES = import.meta.glob<string>("../recipes/prompts/*.md", {
  query: "?raw",
  import: "default",
  eager: true,
});
const DATA = import.meta.glob<Record<string, unknown>>("../recipes/data/*.json", {
  import: "default",
  eager: true,
});
const BUILDS = import.meta.glob<{ default: ComponentType<{ data: unknown }> }>(
  "../recipes/out/*.tsx",
  { eager: true },
);

const RUNS = runsFile as Run[];
const GUIDES = ["README", "TEMPLATE"];
const WIDTHS: [Width, string][] = [
  ["lane", "Lane"],
  ["column", "Column"],
  ["wide", "Wide"],
  ["fill", "Fill"],
];

function parse(source: string): Section[] {
  const sections: Section[] = [];
  const lines = source.split("\n");
  const push = (block: Block) => sections.at(-1)?.blocks.push(block);
  let at = 0;
  while (at < lines.length) {
    const line = lines[at];
    if (line.startsWith("## ")) {
      sections.push({ title: line.slice(3).trim(), blocks: [] });
      at += 1;
    } else if (line.startsWith("```")) {
      const body: string[] = [];
      at += 1;
      while (at < lines.length && !lines[at].startsWith("```")) {
        body.push(lines[at]);
        at += 1;
      }
      push({ kind: "code", text: body.join("\n") });
      at += 1;
    } else if (line.startsWith("- ")) {
      const items: string[] = [];
      while (at < lines.length && (lines[at].startsWith("- ") || (items.length > 0 && /^\s+\S/.test(lines[at])))) {
        if (lines[at].startsWith("- ")) items.push(lines[at].slice(2).trim());
        else items[items.length - 1] += ` ${lines[at].trim()}`;
        at += 1;
      }
      push({ kind: "list", items });
    } else if (line.startsWith("#") || line.trim() === "") {
      at += 1;
    } else {
      const text: string[] = [];
      while (at < lines.length && lines[at].trim() !== "" && !/^(#|- |```)/.test(lines[at])) {
        text.push(lines[at].trim());
        at += 1;
      }
      push({ kind: "paragraph", text: text.join(" ") });
    }
  }
  return sections;
}

function inline(text: string): ReactNode[] {
  return text.split(/`([^`]+)`/).map((part, at) => (at % 2 === 1 ? <code key={at}>{part}</code> : part));
}

function Markdown({ sections }: { sections: Section[] }) {
  return (
    <Prose width="full">
      {sections.map((section) => (
        <Fragment key={section.title}>
          <h2>{section.title}</h2>
          {section.blocks.map((block, at) => {
            switch (block.kind) {
              case "code":
                return (
                  <CodeBlock key={at} variant="plain" wrap>
                    {block.text}
                  </CodeBlock>
                );
              case "list":
                return (
                  <ul key={at}>
                    {block.items.map((item) => (
                      <li key={item}>{inline(item)}</li>
                    ))}
                  </ul>
                );
              case "paragraph":
                return <p key={at}>{inline(block.text)}</p>;
            }
          })}
        </Fragment>
      ))}
    </Prose>
  );
}

/** The sample data with every list and every nested record emptied, to prove the empty state. */
export function emptied(data: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(data).map(([key, value]) => {
      if (Array.isArray(value)) return [key, []];
      if (value !== null && typeof value === "object") return [key, {}];
      return [key, value];
    }),
  );
}

function read(path: string, source: string): Recipe {
  const name = path.slice(path.lastIndexOf("/") + 1).replace(/\.md$/, "");
  const sections = parse(source);
  return {
    name,
    title: name[0].toUpperCase() + name.slice(1),
    intent:
      sections
        .find((section) => section.title === "Intent")
        ?.blocks.find((block) => block.kind === "paragraph")?.text ?? "",
    sections: sections.filter((section) => section.title !== "Intent"),
    data: DATA[`../recipes/data/${name}.json`] ?? {},
    build: BUILDS[`../recipes/out/${name}.tsx`]?.default,
  };
}

const RECIPES: Recipe[] = Object.entries(SOURCES)
  .map(([path, source]) => read(path, source))
  .filter((recipe) => !GUIDES.includes(recipe.name))
  .sort((one, other) => one.name.localeCompare(other.name));

function Build({ recipe }: { recipe: Recipe }) {
  const [sample, setSample] = useState(true);
  const [width, setWidth] = useState<Width>("fill");
  const [scheme, setScheme] = useState<"light" | "dark">("dark");
  const App = recipe.build;
  if (!App) {
    return (
      <Prose>
        <p>No build yet.</p>
      </Prose>
    );
  }
  return (
    <>
      <div className="blk-recipe-toolbar">
        <div className="blk-recipe-toggle" role="group" aria-label="Data">
          <button type="button" aria-pressed={sample} onClick={() => setSample(true)}>
            Sample
          </button>
          <button type="button" aria-pressed={!sample} onClick={() => setSample(false)}>
            Empty
          </button>
        </div>
        <div className="blk-recipe-toggle" role="group" aria-label="Width">
          {WIDTHS.map(([value, label]) => (
            <button key={value} type="button" aria-pressed={width === value} onClick={() => setWidth(value)}>
              {label}
            </button>
          ))}
        </div>
        <div className="blk-recipe-toggle" role="group" aria-label="Scheme">
          <button type="button" aria-pressed={scheme === "light"} onClick={() => setScheme("light")}>
            Light
          </button>
          <button type="button" aria-pressed={scheme === "dark"} onClick={() => setScheme("dark")}>
            Dark
          </button>
        </div>
      </div>
      <div className="blk-recipe-stage">
        <div className="blk-recipe-scheme" data-scheme={scheme} data-width={width}>
          <div className="blk-recipe-frame">
            <App data={sample ? recipe.data : emptied(recipe.data)} />
          </div>
        </div>
      </div>
    </>
  );
}

function RecipeView({ recipe }: { recipe: Recipe }) {
  const runs = RUNS.filter((run) => run.recipe === recipe.name);
  return (
    <article className="blk-docs-page">
      <header>
        <h1>{recipe.title}</h1>
        <p>{recipe.intent}</p>
      </header>
      <div className="blk-recipe-brief">
        <Markdown sections={recipe.sections} />
        <div className="blk-recipe-data">
          <CodeBlock language="json">{JSON.stringify(recipe.data, null, 2)}</CodeBlock>
        </div>
      </div>
      <DocSection title="Build">
        <Build recipe={recipe} />
      </DocSection>
      <DocSection title="Runs">
        {runs.length ? (
          <ItemGroup>
            {runs.map((run) => (
              <Fragment key={`${run.round} ${run.agent}`}>
                <Item accent={run.verdict === "pass" ? "primary" : "muted"}>
                  <ItemContent>
                    <ItemTitle>Round {run.round}</ItemTitle>
                    {run.notes.map((note) => (
                      <ItemDescription key={note} lines={3}>
                        {note}
                      </ItemDescription>
                    ))}
                    <ItemMeta>
                      {[run.agent, run.at.slice(0, 10), run.width].filter(Boolean).join(" • ")}
                    </ItemMeta>
                  </ItemContent>
                  <ItemActions>
                    <Tag>{run.verdict}</Tag>
                  </ItemActions>
                </Item>
                {run.checks?.length ? (
                  <div className="blk-recipe-checks">
                    <ItemGroup flush>
                      {run.checks.map((check) => (
                        <Item key={check.line} size="xs">
                          <ItemMedia variant="status">
                            <StatusIcon status={check.pass ? "done" : "ready"} />
                          </ItemMedia>
                          <ItemContent>
                            <ItemTitle>{check.line}</ItemTitle>
                          </ItemContent>
                        </Item>
                      ))}
                    </ItemGroup>
                  </div>
                ) : null}
              </Fragment>
            ))}
          </ItemGroup>
        ) : (
          <Prose>
            <p>A round appears here once a builder agent records it in runs.json.</p>
          </Prose>
        )}
      </DocSection>
    </article>
  );
}

/** One recipe at a time: what it asks for, its sample data, the build made from it, and its rounds. */
export function RecipesPage({ path }: { path?: string }) {
  const current = RECIPES.find((recipe) => recipe.name === path) ?? RECIPES.at(0);
  return (
    <div className="blk-recipes">
      <nav className="blk-recipes-nav" aria-label="Recipes">
        {RECIPES.map((recipe) => (
          <a
            key={recipe.name}
            href={`#/recipes/${recipe.name}`}
            aria-current={recipe.name === current?.name ? "page" : undefined}
          >
            {recipe.title}
          </a>
        ))}
      </nav>
      {current ? (
        <RecipeView key={current.name} recipe={current} />
      ) : (
        <Prose>
          <p>A recipe appears here once its markdown lands in src/blocks/recipes.</p>
        </Prose>
      )}
    </div>
  );
}
