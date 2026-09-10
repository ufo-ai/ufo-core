import { DocPage, DocSection, Example, PropsTable, type PropRow } from "@/blocks/docs/Docs";

import { StatBasic } from "@/blocks/docs/examples/stat-basic";
import statBasic from "@/blocks/docs/examples/stat-basic.tsx?raw";
import { StatTiles } from "@/blocks/docs/examples/stat-tiles";
import statTiles from "@/blocks/docs/examples/stat-tiles.tsx?raw";
import { StatProgress } from "@/blocks/docs/examples/stat-progress";
import statProgress from "@/blocks/docs/examples/stat-progress.tsx?raw";
import { StatRowsExample } from "@/blocks/docs/examples/stat-rows";
import statRows from "@/blocks/docs/examples/stat-rows.tsx?raw";
import { StatSparkline } from "@/blocks/docs/examples/stat-sparkline";
import statSparkline from "@/blocks/docs/examples/stat-sparkline.tsx?raw";
import { StatBarReport } from "@/blocks/docs/examples/stat-bar-report";
import statBarReport from "@/blocks/docs/examples/stat-bar-report.tsx?raw";
import { StatMedia } from "@/blocks/docs/examples/stat-media";
import statMedia from "@/blocks/docs/examples/stat-media.tsx?raw";
import { StatSentence } from "@/blocks/docs/examples/stat-sentence";
import statSentence from "@/blocks/docs/examples/stat-sentence.tsx?raw";
import { StatMono } from "@/blocks/docs/examples/stat-mono";
import statMono from "@/blocks/docs/examples/stat-mono.tsx?raw";
import { StatDeltaTones } from "@/blocks/docs/examples/stat-delta";
import statDelta from "@/blocks/docs/examples/stat-delta.tsx?raw";
import { StatStates } from "@/blocks/docs/examples/stat-states";
import statStates from "@/blocks/docs/examples/stat-states.tsx?raw";
import { StatStatesInteractive } from "@/blocks/docs/examples/stat-states-interactive";
import statStatesInteractive from "@/blocks/docs/examples/stat-states-interactive.tsx?raw";

const STAT_ROWS: PropRow[] = [
  { name: "label", type: "ReactNode", description: "Names the measure, set above it. Takes a tag or a second phrase beside the words." },
  { name: "value", type: "string", description: "The measure itself, in tabular figures." },
  { name: "media", type: "ReactNode", description: "A 16px source mark drawn before the label." },
  {
    name: "delta",
    type: '{ value: string; direction: "up" | "down" | "flat"; tone?: DeltaTone }',
    description: "The change against the period before, rendered as a Delta.",
  },
  { name: "sub", type: "string", description: "A caption under the value, wrapping to three lines." },
  {
    name: "size",
    type: '"default" | "sm" | "lg"',
    default: '"default"',
    description: "Sets the value at 22px, 15px, or 32px.",
  },
  {
    name: "casing",
    type: '"upper" | "sentence"',
    default: '"upper"',
    description: "Upper is the 11px tracked label; sentence is 13px in the secondary ink, cased as written.",
  },
  {
    name: "font",
    type: '"sans" | "mono"',
    default: '"sans"',
    description: "Sets the value in the mono face.",
  },
];

const API: { name: string; rows: PropRow[] }[] = [
  { name: "Stat", rows: STAT_ROWS },
  { name: "StatTile", rows: STAT_ROWS },
  {
    name: "Delta",
    rows: [
      { name: "value", type: "string", description: "The figure, written with its sign." },
      {
        name: "direction",
        type: '"up" | "down" | "flat"',
        description: "Picks the glyph, so the direction reaches a reader who cannot see the colour.",
      },
      {
        name: "tone",
        type: '"auto" | "positive" | "negative" | "neutral"',
        default: '"auto"',
        description: "Auto colours up primary, down secondary and flat secondary ink; a tone names the colour instead, for a measure where down is the good direction.",
      },
    ],
  },
  {
    name: "StatGrid",
    rows: [
      {
        name: "columns",
        type: "2 | 3 | 4",
        default: "2",
        description: "The most tiles a row holds. The grid halves that under 720px and drops to one under 360px.",
      },
      { name: "children", type: "ReactNode", description: "The tiles." },
    ],
  },
  {
    name: "ProgressStat",
    rows: [
      { name: "label", type: "string", description: "Names the measure, set in uppercase above it." },
      { name: "value", type: "string", description: "Where the measure stands now." },
      { name: "percent", type: "number", description: "The share of the target reached, drawn as the bar's width." },
      { name: "target", type: "string", description: "What the measure is counted towards, shown at the right of the footer." },
      {
        name: "achievedLabel",
        type: "string",
        default: "`${percent}% achieved`",
        description: "Replaces the footer's left text.",
      },
    ],
  },
  {
    name: "Sparkline",
    rows: [
      { name: "values", type: "number[]", description: "The run to draw, scaled against its own peak." },
      {
        name: "kind",
        type: '"bars" | "line"',
        description: "Bars are rounded at the top and grade their fill by rank; a line joins the points.",
      },
      { name: "width", type: "number", default: "88", description: "Drawing width in pixels." },
      { name: "height", type: "number", default: "32", description: "Drawing height in pixels." },
    ],
  },
  {
    name: "StatRow",
    rows: [
      { name: "title", type: "string", description: "The name of the thing counted." },
      { name: "sub", type: "string", description: "What the count is." },
      { name: "children", type: "ReactNode", description: "Sits at the right of the row, usually a Sparkline." },
    ],
  },
  {
    name: "StatRows",
    rows: [
      { name: "fade", type: "boolean", default: "false", description: "Fades the stack out towards its foot." },
      { name: "children", type: "ReactNode", description: "The rows." },
    ],
  },
];

export function StatPage() {
  return (
    <DocPage title="Stat" description="Numbers, progress and sparklines for dashboards.">
      <Example title="Stat" code={statBasic}>
        <StatBasic />
      </Example>
      <Example title="Stat tiles" code={statTiles}>
        <StatTiles />
      </Example>
      <Example title="Progress" code={statProgress}>
        <StatProgress />
      </Example>
      <Example title="Rows with sparklines" code={statRows}>
        <StatRowsExample />
      </Example>
      <Example title="Sparkline" code={statSparkline} align="start">
        <StatSparkline />
      </Example>
      <Example title="Bar chart with tiles and action" code={statBarReport}>
        <StatBarReport />
      </Example>
      <Example
        title="Source mark"
        description="The mark sits 16px before the label, and the label takes nodes as well as words."
        code={statMedia}
        align="start"
      >
        <StatMedia />
      </Example>
      <Example
        title="Sentence label"
        description="A label read as a phrase rather than a heading."
        code={statSentence}
        align="start"
      >
        <StatSentence />
      </Example>
      <Example
        title="Mono figure"
        description="The mono face holds a run of figures in one column; the caption wraps to three lines."
        code={statMono}
        align="start"
      >
        <StatMono />
      </Example>
      <Example
        title="Delta"
        description="The signed figure on its own, beside text or inside a cell."
        code={statDelta}
        align="start"
      >
        <StatDeltaTones />
      </Example>
      <DocSection
        title="States"
        description="The grid reads its own box, not the window, so a lane and a page are the same grid at two widths."
      >
        <Example
          title="Every state"
          description="The same four-column grid at 880, 640 and 320: four columns, then two, then one."
          code={statStates}
          align="start"
        >
          <StatStates />
        </Example>
        <Example
          title="Interactive states"
          description="The chips set the box width and the grid answers with its column count."
          code={statStatesInteractive}
          align="start"
        >
          <StatStatesInteractive />
        </Example>
      </DocSection>
      <DocSection title="API" description="Chart is documented on its own page.">
        <div className="blk-docs-api">
          {API.map((part) => (
            <div key={part.name}>
              <h3 className="blk-docs-api-name">{part.name}</h3>
              <PropsTable rows={part.rows} />
            </div>
          ))}
        </div>
      </DocSection>
    </DocPage>
  );
}
