import { CodeBlock, Prose } from "@/blocks/typography";

const SNIPPET = `import { Prose } from "@/blocks/typography";

export function ReleaseNotes({ body }: { body: ReactNode }) {
  return <Prose width="prose">{body}</Prose>;
}`;

export default function TypographyCodeBlock() {
  return (
    <Prose>
      <CodeBlock language="tsx">{SNIPPET}</CodeBlock>
    </Prose>
  );
}
