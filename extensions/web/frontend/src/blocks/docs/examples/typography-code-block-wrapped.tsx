import { CodeBlock, Prose } from "@/blocks/typography";

const SNIPPET = `{ type: "muted", text: "- Local:   http://localhost:3000" },
{ type: "muted", text: "- Network: http://192.168.1.12:3000" },
setLines((current) => [...current, { type: "output", text: "" }, { type: "command", text: value }]);`;

export default function TypographyCodeBlockWrapped() {
  return (
    <Prose width="full">
      <CodeBlock variant="plain" wrap>
        {SNIPPET}
      </CodeBlock>
    </Prose>
  );
}
