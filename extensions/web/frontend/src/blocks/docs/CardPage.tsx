import { DocPage, DocSection, Example, PropsTable } from "@/blocks/docs/Docs";
import CardDefault from "@/blocks/docs/examples/card-default";
import CardVariants from "@/blocks/docs/examples/card-variants";
import CardWithAction from "@/blocks/docs/examples/card-action";
import CardSmall from "@/blocks/docs/examples/card-small";
import CardLarge from "@/blocks/docs/examples/card-large";
import CardSpacing from "@/blocks/docs/examples/card-spacing";
import CardImageExample from "@/blocks/docs/examples/card-image";
import CardBleed from "@/blocks/docs/examples/card-bleed";
import CardItems from "@/blocks/docs/examples/card-items";
import CardSummary from "@/blocks/docs/examples/card-summary";
import CardMono from "@/blocks/docs/examples/card-mono";
import CardRows from "@/blocks/docs/examples/card-rows";
import CardPill from "@/blocks/docs/examples/card-pill";
import CardBubble from "@/blocks/docs/examples/card-bubble";
import CardRtl from "@/blocks/docs/examples/card-rtl";
import defaultSource from "./examples/card-default.tsx?raw";
import variantsSource from "./examples/card-variants.tsx?raw";
import actionSource from "./examples/card-action.tsx?raw";
import smallSource from "./examples/card-small.tsx?raw";
import largeSource from "./examples/card-large.tsx?raw";
import spacingSource from "./examples/card-spacing.tsx?raw";
import imageSource from "./examples/card-image.tsx?raw";
import bleedSource from "./examples/card-bleed.tsx?raw";
import itemsSource from "./examples/card-items.tsx?raw";
import summarySource from "./examples/card-summary.tsx?raw";
import monoSource from "./examples/card-mono.tsx?raw";
import rowsSource from "./examples/card-rows.tsx?raw";
import pillSource from "./examples/card-pill.tsx?raw";
import bubbleSource from "./examples/card-bubble.tsx?raw";
import rtlSource from "./examples/card-rtl.tsx?raw";
import { CardButtonStates } from "@/blocks/docs/examples/card-button-states";
import buttonStatesSource from "./examples/card-button-states.tsx?raw";
import { CardStates } from "@/blocks/docs/examples/card-states";
import cardStatesSource from "./examples/card-states.tsx?raw";
import { CardStatesInteractive } from "@/blocks/docs/examples/card-states-interactive";
import cardStatesInteractiveSource from "./examples/card-states-interactive.tsx?raw";

export function CardPage() {
  return (
    <DocPage title="Card" description="A bordered surface with header, content and footer slots.">
      <Example title="Default" code={defaultSource} align="start">
        <CardDefault />
      </Example>
      <Example
        title="Variants"
        description="Outline draws an edge, muted fills the ground, plain keeps only the padding."
        code={variantsSource}
        align="start"
      >
        <CardVariants />
      </Example>
      <Example
        title="With action"
        description="The action holds the trailing edge of the header, whatever the title runs to."
        code={actionSource}
        align="start"
      >
        <CardWithAction />
      </Example>
      <Example title="Small" description="The 12px inset, for a card that sits inside another surface." code={smallSource} align="start">
        <CardSmall />
      </Example>
      <Example
        title="Large"
        description="The 24px inset and the h1 title, closing on a single 44px act that fills the foot."
        code={largeSource}
        align="start"
      >
        <CardLarge />
      </Example>
      <Example
        title="Spacing"
        description="Every inset and gap reads --blk-card-spacing, so one declaration retunes the card."
        code={spacingSource}
        align="start"
      >
        <CardSpacing />
      </Example>
      <Example
        title="Image"
        description="The picture meets the card's two upper corners and the header follows it."
        code={imageSource}
        align="start"
      >
        <CardImageExample />
      </Example>
      <Example
        title="Edge-to-edge"
        description="Bleed pulls one content block out to the card edge; the blocks around it keep the inset."
        code={bleedSource}
        align="start"
      >
        <CardBleed />
      </Example>
      <Example
        title="Item list"
        description="A flush group closes the card without leaving a rule under the last row."
        code={itemsSource}
        align="start"
      >
        <CardItems />
      </Example>
      <Example
        title="Summary with prompts"
        description="A paragraph the agent wrote, then the prompts it offers next."
        code={summarySource}
        align="start"
      >
        <CardSummary />
      </Example>
      <Example title="Mono body" description="The same card in the mono face, as the Coding lane sets it." code={monoSource} align="start">
        <CardMono />
      </Example>
      <Example title="Nested list" description="Rows carry their own rule, so the content closes its gap." code={rowsSource} align="start">
        <CardRows />
      </Example>
      <Example
        title="Pill"
        description="A 40px chip that hugs its content: a leading mark, one line, and the mark that opens it."
        code={pillSource}
        align="start"
      >
        <CardPill />
      </Example>
      <Example
        title="Bubble"
        description="An inline card aligned to the end of its column, held to 72 percent of the width."
        code={bubbleSource}
        align="start"
      >
        <CardBubble />
      </Example>
      <Example
        title="Button sizes and states"
        description="The 28px act a row of them fits, and the one refused: dimmed to .5, holding its ground under the pointer."
        code={buttonStatesSource}
        align="start"
      >
        <CardButtonStates />
      </Example>
      <Example
        title="RTL"
        description="The card reads from the other edge: the action, the acts and the bleed all mirror."
        code={rtlSource}
        align="start"
      >
        <CardRtl />
      </Example>
      <DocSection
        title="States"
        description="The card takes the width its container gives it: nothing inside states a width, and the footer wraps rather than overflowing."
      >
        <Example
          title="Every state"
          description="One card at 640 and at 320. The footer runs its acts onto a second line rather than past the edge."
          code={cardStatesSource}
          align="start"
        >
          <CardStates />
        </Example>
        <Example
          title="Interactive states"
          description="The chips set the container width and the act folds the body, so the card reflows under both."
          code={cardStatesInteractiveSource}
          align="start"
        >
          <CardStatesInteractive />
        </Example>
      </DocSection>
      <DocSection title="API">
        <h3>Card</h3>
        <PropsTable
          rows={[
            {
              name: "variant",
              type: '"outline" | "muted" | "plain" | "pill"',
              default: '"outline"',
              description: "A hairline box, a filled box, padding alone, or the 40px chip that hugs its content.",
            },
            {
              name: "size",
              type: '"default" | "sm" | "lg"',
              default: '"default"',
              description: "Spacing of 16px, 12px, or 24px for a card that carries a report.",
            },
            {
              name: "inline",
              type: "boolean",
              default: "false",
              description: "Hugs the content instead of filling the column.",
            },
            {
              name: "align",
              type: '"start" | "end"',
              description: "Which edge of the column the card sits against.",
            },
            {
              name: "--blk-card-spacing",
              type: "length",
              default: "16px",
              description: "The inset, every gap under it, and how far a bleed reaches.",
            },
          ]}
        />
        <h3>CardImage</h3>
        <PropsTable
          rows={[
            { name: "src", type: "string", description: "The picture." },
            { name: "alt", type: "string", description: "What the picture shows, for a reader who cannot see it." },
            { name: "ratio", type: "number", default: "16 / 9", description: "The aspect the picture is cut to." },
          ]}
        />
        <h3>CardHeader</h3>
        <PropsTable
          rows={[
            {
              name: "children",
              type: "ReactNode",
              description: "Title, description and an optional CardAction, stacked at a 4px gap.",
            },
          ]}
        />
        <h3>CardTitle</h3>
        <PropsTable
          rows={[
            {
              name: "size",
              type: '"default" | "lg"',
              default: '"default"',
              description: "15px at the label step, or 22px at the h1 step.",
            },
          ]}
        />
        <h3>CardDescription</h3>
        <PropsTable
          rows={[{ name: "children", type: "ReactNode", description: "The line under the title, set at 13px in the secondary ink." }]}
        />
        <h3>CardAction</h3>
        <PropsTable
          rows={[
            {
              name: "children",
              type: "ReactNode",
              description: "What the header offers at its trailing edge, spanning every row the header has.",
            },
          ]}
        />
        <h3>CardContent</h3>
        <PropsTable
          rows={[
            {
              name: "bleed",
              type: "boolean",
              default: "false",
              description: "Runs the block out to the card edge by the card's own spacing.",
            },
            {
              name: "data-font",
              type: '"mono"',
              description: "Sets the mono face for the content. Absent, the content reads in the sans face.",
            },
          ]}
        />
        <h3>CardFooter</h3>
        <PropsTable
          rows={[
            {
              name: "children",
              type: "ReactNode",
              description: "Acts in a row under a hairline. A single button fills the width.",
            },
          ]}
        />
        <h3>CardButton</h3>
        <PropsTable
          rows={[
            {
              name: "variant",
              type: '"primary" | "secondary"',
              default: '"primary"',
              description: "Filled on the ink ground, or quiet on the muted one.",
            },
            {
              name: "size",
              type: '"sm" | "default" | "lg"',
              default: '"default"',
              description: "28px for a row of acts, 36px, or 44px for the single act that closes a report.",
            },
            {
              name: "disabled",
              type: "boolean",
              default: "false",
              description: "Refuses the press, reports aria-disabled, dims the act to .5 and holds its ground under the pointer.",
            },
            { name: "onClick", type: "() => void", description: "Called on click." },
          ]}
        />
      </DocSection>
    </DocPage>
  );
}
