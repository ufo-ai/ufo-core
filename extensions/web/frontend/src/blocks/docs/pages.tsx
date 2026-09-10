import type { ReactNode } from "react";

import { HomePage } from "@/blocks/docs/HomePage";
import { TypographyPage } from "@/blocks/docs/TypographyPage";
import { ActionBarPage } from "@/blocks/docs/ActionBarPage";
import { ItemPage } from "@/blocks/docs/ItemPage";
import { CardPage } from "@/blocks/docs/CardPage";
import { TablePage } from "@/blocks/docs/TablePage";
import { StatPage } from "@/blocks/docs/StatPage";
import { ChartPage } from "@/blocks/docs/ChartPage";
import { ControlsPage } from "@/blocks/docs/ControlsPage";
import { CompositionsPage } from "@/blocks/docs/CompositionsPage";
import { RecipesPage } from "@/blocks/docs/RecipesPage";

export type Page = { slug: string; title: string; render: (path?: string) => ReactNode };

export const PAGES: Page[] = [
  { slug: "", title: "Blocks", render: () => <HomePage pages={PAGES} /> },
  { slug: "typography", title: "Typography", render: () => <TypographyPage /> },
  { slug: "action-bar", title: "Action Bar", render: () => <ActionBarPage /> },
  { slug: "item", title: "Item", render: () => <ItemPage /> },
  { slug: "card", title: "Card", render: () => <CardPage /> },
  { slug: "table", title: "Table", render: () => <TablePage /> },
  { slug: "stat", title: "Stat", render: () => <StatPage /> },
  { slug: "chart", title: "Chart", render: () => <ChartPage /> },
  { slug: "controls", title: "Controls", render: () => <ControlsPage /> },
  { slug: "compositions", title: "Compositions", render: () => <CompositionsPage /> },
  { slug: "recipes", title: "Recipes", render: (path) => <RecipesPage path={path} /> },
];
