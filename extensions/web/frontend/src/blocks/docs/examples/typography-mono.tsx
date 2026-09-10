import { Prose } from "@/blocks/typography";

export default function TypographyMono() {
  return (
    <Prose font="mono">
      <h2>Run the migration</h2>
      <p>
        The migrate job runs to completion before the fleet rolls. Outgoing pods keep serving until the new ones are
        ready, so the schema a revision leaves still answers the release it replaces.
      </p>
      <p>
        Start the job with <code>ufoctl migrate</code> and roll the fleet behind it.
      </p>
    </Prose>
  );
}
