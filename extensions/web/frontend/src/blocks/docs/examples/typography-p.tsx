import { Prose } from "@/blocks/typography";

export default function TypographyP() {
  return (
    <Prose>
      <p>
        The agent watches the connected channel. A message that reads as a defect starts a draft issue, and the draft is
        shown in the thread before anything is written to the tracker.
      </p>
      <p>
        Approve the draft and the issue is created with the thread attached. Reject it and the draft is dropped; the
        message stays where it was.
      </p>
    </Prose>
  );
}
