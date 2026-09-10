import { DocPage, DocSection, Example, PropsTable, type PropRow } from "@/blocks/docs/Docs";

import { ChartArea } from "@/blocks/docs/examples/chart-area";
import chartArea from "@/blocks/docs/examples/chart-area.tsx?raw";
import { ChartAreaLinear } from "@/blocks/docs/examples/chart-area-linear";
import chartAreaLinear from "@/blocks/docs/examples/chart-area-linear.tsx?raw";
import { ChartAreaStep } from "@/blocks/docs/examples/chart-area-step";
import chartAreaStep from "@/blocks/docs/examples/chart-area-step.tsx?raw";
import { ChartAreaStacked } from "@/blocks/docs/examples/chart-area-stacked";
import chartAreaStacked from "@/blocks/docs/examples/chart-area-stacked.tsx?raw";
import { ChartAreaStackedExpand } from "@/blocks/docs/examples/chart-area-stacked-expand";
import chartAreaStackedExpand from "@/blocks/docs/examples/chart-area-stacked-expand.tsx?raw";
import { ChartAreaLegend } from "@/blocks/docs/examples/chart-area-legend";
import chartAreaLegend from "@/blocks/docs/examples/chart-area-legend.tsx?raw";
import { ChartAreaIcons } from "@/blocks/docs/examples/chart-area-icons";
import chartAreaIcons from "@/blocks/docs/examples/chart-area-icons.tsx?raw";
import { ChartAreaInteractive } from "@/blocks/docs/examples/chart-area-interactive";
import chartAreaInteractive from "@/blocks/docs/examples/chart-area-interactive.tsx?raw";
import { ChartBar } from "@/blocks/docs/examples/chart-bar";
import chartBar from "@/blocks/docs/examples/chart-bar.tsx?raw";
import { ChartBarHorizontal } from "@/blocks/docs/examples/chart-bar-horizontal";
import chartBarHorizontal from "@/blocks/docs/examples/chart-bar-horizontal.tsx?raw";
import { ChartBarMultiple } from "@/blocks/docs/examples/chart-bar-multiple";
import chartBarMultiple from "@/blocks/docs/examples/chart-bar-multiple.tsx?raw";
import { ChartBarLabel } from "@/blocks/docs/examples/chart-bar-label";
import chartBarLabel from "@/blocks/docs/examples/chart-bar-label.tsx?raw";
import { ChartBarLabelCustom } from "@/blocks/docs/examples/chart-bar-label-custom";
import chartBarLabelCustom from "@/blocks/docs/examples/chart-bar-label-custom.tsx?raw";
import { ChartBarMixed } from "@/blocks/docs/examples/chart-bar-mixed";
import chartBarMixed from "@/blocks/docs/examples/chart-bar-mixed.tsx?raw";
import { ChartBarStacked } from "@/blocks/docs/examples/chart-bar-stacked";
import chartBarStacked from "@/blocks/docs/examples/chart-bar-stacked.tsx?raw";
import { ChartBarActive } from "@/blocks/docs/examples/chart-bar-active";
import chartBarActive from "@/blocks/docs/examples/chart-bar-active.tsx?raw";
import { ChartBarNegative } from "@/blocks/docs/examples/chart-bar-negative";
import chartBarNegative from "@/blocks/docs/examples/chart-bar-negative.tsx?raw";
import { ChartBarInteractive } from "@/blocks/docs/examples/chart-bar-interactive";
import chartBarInteractive from "@/blocks/docs/examples/chart-bar-interactive.tsx?raw";
import { ChartLine } from "@/blocks/docs/examples/chart-line";
import chartLine from "@/blocks/docs/examples/chart-line.tsx?raw";
import { ChartLineLinear } from "@/blocks/docs/examples/chart-line-linear";
import chartLineLinear from "@/blocks/docs/examples/chart-line-linear.tsx?raw";
import { ChartLineStep } from "@/blocks/docs/examples/chart-line-step";
import chartLineStep from "@/blocks/docs/examples/chart-line-step.tsx?raw";
import { ChartLineMultiple } from "@/blocks/docs/examples/chart-line-multiple";
import chartLineMultiple from "@/blocks/docs/examples/chart-line-multiple.tsx?raw";
import { ChartLineDots } from "@/blocks/docs/examples/chart-line-dots";
import chartLineDots from "@/blocks/docs/examples/chart-line-dots.tsx?raw";
import { ChartLineLabel } from "@/blocks/docs/examples/chart-line-label";
import chartLineLabel from "@/blocks/docs/examples/chart-line-label.tsx?raw";
import { ChartLineDotsCustom } from "@/blocks/docs/examples/chart-line-dots-custom";
import chartLineDotsCustom from "@/blocks/docs/examples/chart-line-dots-custom.tsx?raw";
import { ChartPie } from "@/blocks/docs/examples/chart-pie";
import chartPie from "@/blocks/docs/examples/chart-pie.tsx?raw";
import { ChartPieDonutText } from "@/blocks/docs/examples/chart-pie-donut-text";
import chartPieDonutText from "@/blocks/docs/examples/chart-pie-donut-text.tsx?raw";
import { ChartPieLegend } from "@/blocks/docs/examples/chart-pie-legend";
import chartPieLegend from "@/blocks/docs/examples/chart-pie-legend.tsx?raw";
import { ChartRadar } from "@/blocks/docs/examples/chart-radar";
import chartRadar from "@/blocks/docs/examples/chart-radar.tsx?raw";
import { ChartRadial } from "@/blocks/docs/examples/chart-radial";
import chartRadial from "@/blocks/docs/examples/chart-radial.tsx?raw";
import { ChartRadialStacked } from "@/blocks/docs/examples/chart-radial-stacked";
import chartRadialStacked from "@/blocks/docs/examples/chart-radial-stacked.tsx?raw";
import { ChartTooltipVariants } from "@/blocks/docs/examples/chart-tooltip-variants";
import chartTooltipVariants from "@/blocks/docs/examples/chart-tooltip-variants.tsx?raw";
import { ChartEmpty } from "@/blocks/docs/examples/chart-empty";
import chartEmpty from "@/blocks/docs/examples/chart-empty.tsx?raw";
import { ChartLoading } from "@/blocks/docs/examples/chart-loading";
import chartLoading from "@/blocks/docs/examples/chart-loading.tsx?raw";
import { ChartTheming } from "@/blocks/docs/examples/chart-theming";
import chartTheming from "@/blocks/docs/examples/chart-theming.tsx?raw";
import { ChartAxisTick } from "@/blocks/docs/examples/chart-axis-tick";
import chartAxisTick from "@/blocks/docs/examples/chart-axis-tick.tsx?raw";
import { ChartRatioExample } from "@/blocks/docs/examples/chart-ratio";
import chartRatio from "@/blocks/docs/examples/chart-ratio.tsx?raw";

const API: { name: string; rows: PropRow[] }[] = [
  {
    name: "ChartContainer",
    rows: [
      {
        name: "config",
        type: "ChartConfig",
        description: "Names each series and its colour, written out as --blk-color-<key>.",
      },
      {
        name: "ratio",
        type: '"video" | "square" | "half"',
        default: '"video"',
        description: "The box the plot is drawn in: 16/9, 1/1 capped at 260px, or 2/1 capped at 200px.",
      },
      { name: "children", type: "ReactNode", description: "One recharts chart." },
      {
        name: "className",
        type: "string",
        description: "Added to the container; blk-chart-square and blk-chart-half set the same ratios.",
      },
    ],
  },
  {
    name: "ChartConfig",
    rows: [
      { name: "label", type: "ReactNode", description: "The series name, used by the tooltip and the legend." },
      { name: "color", type: "string", description: "One colour for both schemes." },
      {
        name: "theme",
        type: "{ light: string; dark: string }",
        description: "A colour per scheme, in place of color.",
      },
      { name: "icon", type: "ComponentType", description: "Drawn in place of the colour mark." },
    ],
  },
  {
    name: "ChartTick",
    rows: [
      {
        name: "lines",
        type: "(value: string) => [string, string?]",
        description: "Reads the tick's value and returns its first line and, when there is one, the line under it.",
      },
      {
        name: "x, y, payload",
        type: "string | number, string | number, { value?: string | number }",
        description: "Handed over by the axis; spread the tick's own props in.",
      },
    ],
  },
  {
    name: "ChartTooltipContent",
    rows: [
      {
        name: "indicator",
        type: '"dot" | "line" | "dashed"',
        default: '"dot"',
        description: "The shape of each series' mark.",
      },
      { name: "hideLabel", type: "boolean", default: "false", description: "Drops the heading row." },
      { name: "hideIndicator", type: "boolean", default: "false", description: "Drops the marks." },
      { name: "labelKey", type: "string", description: "Reads the heading off this key instead of the axis value." },
      { name: "nameKey", type: "string", description: "Reads each series' name off this key." },
      { name: "labelFormatter", type: "(label, payload) => ReactNode", description: "Rewrites the heading." },
      {
        name: "formatter",
        type: "(value, name, item, index, payload) => ReactNode",
        description: "Draws a row in place of the mark, name and value.",
      },
      { name: "color", type: "string", description: "Overrides the mark colour for every row." },
    ],
  },
  {
    name: "ChartLegendContent",
    rows: [
      { name: "nameKey", type: "string", description: "Reads each entry's name off this key." },
      { name: "hideIcon", type: "boolean", default: "false", description: "Draws the colour mark even where the config names an icon." },
      {
        name: "verticalAlign",
        type: '"top" | "bottom"',
        default: '"bottom"',
        description: "Which side of the plot the legend sits on, set by recharts.",
      },
    ],
  },
  {
    name: "ChartStyle",
    rows: [
      { name: "id", type: "string", description: "The data-chart value the rules are written against." },
      { name: "config", type: "ChartConfig", description: "The series whose colours are written out." },
    ],
  },
];

export function ChartPage() {
  return (
    <DocPage
      title="Chart"
      description="Recharts components with a shared config, tooltip and legend."
    >
      <Example title="Area chart" code={chartArea}>
        <ChartArea />
      </Example>
      <Example title="Area linear" code={chartAreaLinear}>
        <ChartAreaLinear />
      </Example>
      <Example title="Area step" code={chartAreaStep}>
        <ChartAreaStep />
      </Example>
      <Example title="Area stacked" code={chartAreaStacked}>
        <ChartAreaStacked />
      </Example>
      <Example title="Area stacked expanded" code={chartAreaStackedExpand}>
        <ChartAreaStackedExpand />
      </Example>
      <Example title="Area legend" code={chartAreaLegend}>
        <ChartAreaLegend />
      </Example>
      <Example title="Area icons" code={chartAreaIcons}>
        <ChartAreaIcons />
      </Example>
      <Example title="Area interactive" code={chartAreaInteractive}>
        <ChartAreaInteractive />
      </Example>
      <Example title="Bar chart" code={chartBar}>
        <ChartBar />
      </Example>
      <Example title="Bar horizontal" code={chartBarHorizontal}>
        <ChartBarHorizontal />
      </Example>
      <Example title="Bar multiple" code={chartBarMultiple}>
        <ChartBarMultiple />
      </Example>
      <Example title="Bar label" code={chartBarLabel}>
        <ChartBarLabel />
      </Example>
      <Example title="Bar custom label" code={chartBarLabelCustom}>
        <ChartBarLabelCustom />
      </Example>
      <Example title="Bar mixed" code={chartBarMixed}>
        <ChartBarMixed />
      </Example>
      <Example title="Bar stacked" code={chartBarStacked}>
        <ChartBarStacked />
      </Example>
      <Example title="Bar active" code={chartBarActive}>
        <ChartBarActive />
      </Example>
      <Example title="Bar negative" code={chartBarNegative}>
        <ChartBarNegative />
      </Example>
      <Example title="Bar interactive" code={chartBarInteractive}>
        <ChartBarInteractive />
      </Example>
      <Example title="Line chart" code={chartLine}>
        <ChartLine />
      </Example>
      <Example title="Line linear" code={chartLineLinear}>
        <ChartLineLinear />
      </Example>
      <Example title="Line step" code={chartLineStep}>
        <ChartLineStep />
      </Example>
      <Example title="Line multiple" code={chartLineMultiple}>
        <ChartLineMultiple />
      </Example>
      <Example title="Line dots" code={chartLineDots}>
        <ChartLineDots />
      </Example>
      <Example title="Line label" code={chartLineLabel}>
        <ChartLineLabel />
      </Example>
      <Example title="Line custom dots" code={chartLineDotsCustom}>
        <ChartLineDotsCustom />
      </Example>
      <Example title="Pie chart" code={chartPie}>
        <ChartPie />
      </Example>
      <Example title="Pie donut with text" code={chartPieDonutText}>
        <ChartPieDonutText />
      </Example>
      <Example title="Pie legend" code={chartPieLegend}>
        <ChartPieLegend />
      </Example>
      <Example title="Radar chart" code={chartRadar}>
        <ChartRadar />
      </Example>
      <Example title="Radial chart" code={chartRadial}>
        <ChartRadial />
      </Example>
      <Example title="Radial stacked" code={chartRadialStacked}>
        <ChartRadialStacked />
      </Example>
      <Example title="Tooltip variants" code={chartTooltipVariants}>
        <ChartTooltipVariants />
      </Example>
      <Example title="Empty" code={chartEmpty}>
        <ChartEmpty />
      </Example>
      <Example title="Loading" code={chartLoading}>
        <ChartLoading />
      </Example>
      <Example
        title="Two-line axis tick"
        description="The period on the first line, what it was counted out of under it."
        code={chartAxisTick}
      >
        <ChartAxisTick />
      </Example>
      <Example title="Half ratio" description="A 2/1 box, capped at 200px." code={chartRatio}>
        <ChartRatioExample />
      </Example>
      <DocSection
        title="Theming"
        description="A series takes theme instead of color to hold one colour per scheme; ChartStyle writes both against the container's data-chart id."
      >
        <Example code={chartTheming}>
          <ChartTheming />
        </Example>
      </DocSection>
      <DocSection
        title="API"
        description="ChartTooltip and ChartLegend are recharts' Tooltip and Legend; pass ChartTooltipContent or ChartLegendContent to their content prop."
      >
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
