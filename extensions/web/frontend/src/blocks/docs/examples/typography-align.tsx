import { Prose } from "@/blocks/typography";

const WIDE = { width: "100%", maxWidth: "none", display: "flex", flexDirection: "column" as const, gap: 24 };

export default function TypographyAlign() {
  return (
    <div style={WIDE}>
      <Prose>
        <h3>Start</h3>
        <p>
          The column caps its measure at 640 and stays against the leading edge, which is what a lane
          of rows and a reading column beside each other need.
        </p>
      </Prose>
      <Prose align="center">
        <h3>Center</h3>
        <p>
          The same column, centred in a container wider than its measure. A page of copy on its own
          reads from the middle rather than from one edge.
        </p>
      </Prose>
    </div>
  );
}
