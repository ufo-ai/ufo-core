import { Card, CardDescription, CardHeader, CardTitle } from "@/blocks/card";
import type { Page } from "@/blocks/docs/pages";
import typographySource from "@/blocks/docs/TypographyPage.tsx?raw";
import actionBarSource from "@/blocks/docs/ActionBarPage.tsx?raw";
import itemSource from "@/blocks/docs/ItemPage.tsx?raw";
import cardSource from "@/blocks/docs/CardPage.tsx?raw";
import tableSource from "@/blocks/docs/TablePage.tsx?raw";
import statSource from "@/blocks/docs/StatPage.tsx?raw";
import chartSource from "@/blocks/docs/ChartPage.tsx?raw";
import controlsSource from "@/blocks/docs/ControlsPage.tsx?raw";
import compositionsSource from "@/blocks/docs/CompositionsPage.tsx?raw";
import recipesSource from "@/blocks/docs/RecipesPage.tsx?raw";

const BLOCKS: Record<string, { description: string; source: string }> = {
  typography: {
    description: "Headings, paragraphs, lists and code inside a prose column.",
    source: typographySource,
  },
  "action-bar": {
    description: "Lane headers, prompt chips, toolbars and the composer.",
    source: actionBarSource,
  },
  item: {
    description: "List rows carrying media, content, meta and an accent bar.",
    source: itemSource,
  },
  card: {
    description: "One record on its own surface: a header, what it holds, and its acts.",
    source: cardSource,
  },
  table: {
    description: "Rows in folding sections, and the data table that sorts, filters and pages them.",
    source: tableSource,
  },
  stat: {
    description: "A figure, a progress bar, tiles, and the sparkline rows beside them.",
    source: statSource,
  },
  chart: {
    description: "Area, bar, line, pie, radar and radial charts on the block palette.",
    source: chartSource,
  },
  controls: {
    description: "The avatar, the dropdown menu and the checkbox a row reaches for.",
    source: controlsSource,
  },
  compositions: {
    description: "The blocks assembled into the lanes and cards the apps are built from.",
    source: compositionsSource,
  },
  recipes: {
    description: "One brief per app: what it holds, the data behind it, and the build made from it.",
    source: recipesSource,
  },
};

/** The index: every block, what it is for, and how many examples its page holds. */
export function HomePage({ pages }: { pages: Page[] }) {
  return (
    <article className="blk-docs-page">
      <header>
        <h1>Blocks</h1>
        <p>
          The components the apps are built from. Every block is plain CSS over the token set, and
          every variant is one data attribute.
        </p>
      </header>
      <div className="blk-docs-index">
        {pages
          .filter((page) => BLOCKS[page.slug])
          .map((page) => {
            const block = BLOCKS[page.slug];
            const examples = block.source.split("<Example").length - 1;
            return (
              <a key={page.slug} className="blk-docs-index-card" href={`#/${page.slug}`}>
                <Card variant="outline">
                  <CardHeader>
                    <CardTitle>{page.title}</CardTitle>
                    <CardDescription>{block.description}</CardDescription>
                  </CardHeader>
                  <span className="blk-docs-index-count">
                    {examples} {examples === 1 ? "example" : "examples"}
                  </span>
                </Card>
              </a>
            );
          })}
      </div>
    </article>
  );
}
