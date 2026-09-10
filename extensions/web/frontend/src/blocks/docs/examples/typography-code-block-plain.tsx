import { CodeBlock, Prose } from "@/blocks/typography";

const SNIPPET = `const initialLines: TerminalLine[] = [
  { type: "command", text: "pnpm dev" },
  { type: "output", text: "Next.js 16.0" },
  { type: "success", text: "Ready in 482ms" },
];`;

export default function TypographyCodeBlockPlain() {
  return (
    <Prose width="full">
      <CodeBlock variant="plain">{SNIPPET}</CodeBlock>
    </Prose>
  );
}
