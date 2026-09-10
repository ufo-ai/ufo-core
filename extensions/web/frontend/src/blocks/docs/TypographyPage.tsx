import { DocPage, DocSection, Example, PropsTable } from "@/blocks/docs/Docs";
import TypographyDocument from "@/blocks/docs/examples/typography-document";
import TypographyH1 from "@/blocks/docs/examples/typography-h1";
import TypographyH2 from "@/blocks/docs/examples/typography-h2";
import TypographyH3 from "@/blocks/docs/examples/typography-h3";
import TypographyH4 from "@/blocks/docs/examples/typography-h4";
import TypographyP from "@/blocks/docs/examples/typography-p";
import TypographyBlockquote from "@/blocks/docs/examples/typography-blockquote";
import TypographyTable from "@/blocks/docs/examples/typography-table";
import TypographyList from "@/blocks/docs/examples/typography-list";
import TypographyInlineCode from "@/blocks/docs/examples/typography-inline-code";
import TypographyLead from "@/blocks/docs/examples/typography-lead";
import TypographyLarge from "@/blocks/docs/examples/typography-large";
import TypographySmall from "@/blocks/docs/examples/typography-small";
import TypographyMuted from "@/blocks/docs/examples/typography-muted";
import TypographyCodeBlock from "@/blocks/docs/examples/typography-code-block";
import TypographyCodeBlockPlain from "@/blocks/docs/examples/typography-code-block-plain";
import TypographyCodeBlockWrapped from "@/blocks/docs/examples/typography-code-block-wrapped";
import TypographyMono from "@/blocks/docs/examples/typography-mono";
import TypographyAlign from "@/blocks/docs/examples/typography-align";
import documentSource from "./examples/typography-document.tsx?raw";
import h1Source from "./examples/typography-h1.tsx?raw";
import h2Source from "./examples/typography-h2.tsx?raw";
import h3Source from "./examples/typography-h3.tsx?raw";
import h4Source from "./examples/typography-h4.tsx?raw";
import pSource from "./examples/typography-p.tsx?raw";
import blockquoteSource from "./examples/typography-blockquote.tsx?raw";
import tableSource from "./examples/typography-table.tsx?raw";
import listSource from "./examples/typography-list.tsx?raw";
import inlineCodeSource from "./examples/typography-inline-code.tsx?raw";
import leadSource from "./examples/typography-lead.tsx?raw";
import largeSource from "./examples/typography-large.tsx?raw";
import smallSource from "./examples/typography-small.tsx?raw";
import mutedSource from "./examples/typography-muted.tsx?raw";
import codeBlockSource from "./examples/typography-code-block.tsx?raw";
import codeBlockPlainSource from "./examples/typography-code-block-plain.tsx?raw";
import codeBlockWrappedSource from "./examples/typography-code-block-wrapped.tsx?raw";
import monoSource from "./examples/typography-mono.tsx?raw";
import alignSource from "./examples/typography-align.tsx?raw";

export function TypographyPage() {
  return (
    <DocPage title="Typography" description="Styles for headings, paragraphs, lists and code inside a prose column.">
      <Example
        title="Document"
        description="A page of copy: one heading, sections separated by a ruled row, a figure and a numbered list."
        code={documentSource}
        align="start"
      >
        <TypographyDocument />
      </Example>
      <Example title="h1" code={h1Source} align="start">
        <TypographyH1 />
      </Example>
      <Example title="h2" code={h2Source} align="start">
        <TypographyH2 />
      </Example>
      <Example title="h3" code={h3Source} align="start">
        <TypographyH3 />
      </Example>
      <Example title="h4" code={h4Source} align="start">
        <TypographyH4 />
      </Example>
      <Example title="p" code={pSource} align="start">
        <TypographyP />
      </Example>
      <Example title="blockquote" code={blockquoteSource} align="start">
        <TypographyBlockquote />
      </Example>
      <Example title="table" code={tableSource} align="start">
        <TypographyTable />
      </Example>
      <Example title="list" code={listSource} align="start">
        <TypographyList />
      </Example>
      <Example title="Inline code" code={inlineCodeSource} align="start">
        <TypographyInlineCode />
      </Example>
      <Example title="Lead" code={leadSource} align="start">
        <TypographyLead />
      </Example>
      <Example title="Large" code={largeSource} align="start">
        <TypographyLarge />
      </Example>
      <Example title="Small" code={smallSource} align="start">
        <TypographySmall />
      </Example>
      <Example title="Muted" code={mutedSource} align="start">
        <TypographyMuted />
      </Example>
      <Example title="Code block" code={codeBlockSource} align="start">
        <TypographyCodeBlock />
      </Example>
      <Example
        title="Code block plain"
        description="No ground, no padding: the listing reads as the page's own text, as the Coding lane sets it."
        code={codeBlockPlainSource}
        align="start"
      >
        <TypographyCodeBlockPlain />
      </Example>
      <Example
        title="Code block wrapped"
        description="Long lines fold into the column instead of scrolling sideways."
        code={codeBlockWrappedSource}
        align="start"
      >
        <TypographyCodeBlockWrapped />
      </Example>
      <Example
        title="Alignment"
        description="The column holds the leading edge by default; center puts it in the middle of a container wider than its measure."
        code={alignSource}
        align="start"
      >
        <TypographyAlign />
      </Example>
      <Example
        title="Mono body"
        description="The Coding lane reads its whole column in the mono face, so inline code keeps that face and size."
        code={monoSource}
        align="start"
      >
        <TypographyMono />
      </Example>
      <DocSection title="API">
        <h3>Prose</h3>
        <PropsTable
          rows={[
            {
              name: "width",
              type: '"prose" | "full"',
              default: '"prose"',
              description: "Caps the column at 640px, or lets it fill its container.",
            },
            {
              name: "font",
              type: '"sans" | "mono"',
              default: '"sans"',
              description: "Face for the whole column. The Coding lane sets mono.",
            },
            {
              name: "align",
              type: '"start" | "center"',
              default: '"start"',
              description: "Center puts the capped column in the middle of a container wider than 640px.",
            },
          ]}
        />
        <h3>CodeBlock</h3>
        <PropsTable
          rows={[
            {
              name: "language",
              type: "string",
              description: "Names the language on the listing and on the code element as language-<name>.",
            },
            {
              name: "variant",
              type: '"filled" | "plain"',
              default: '"filled"',
              description: "Plain drops the ground, the padding and the radius, leaving the mono text on the page.",
            },
            {
              name: "wrap",
              type: "boolean",
              default: "false",
              description: "Folds long lines into the column instead of scrolling them sideways.",
            },
          ]}
        />
        <h3>Figure</h3>
        <PropsTable
          rows={[
            { name: "src", type: "string", description: "Image source. The image is centred and never cropped." },
            { name: "alt", type: "string", description: "Alternative text. Required." },
            { name: "caption", type: "string", description: "Caption under the image. Omitted when absent." },
          ]}
        />
      </DocSection>
    </DocPage>
  );
}
