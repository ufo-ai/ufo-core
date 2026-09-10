import { Prose } from "@/blocks/typography";

export default function TypographyInlineCode() {
  return (
    <Prose>
      <p>
        Start the stack with <code>ufoctl serve</code>. Every authenticated read carries the session in{" "}
        <code>x-ufo-session</code>.
      </p>
    </Prose>
  );
}
