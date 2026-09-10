import { Prose } from "@/blocks/typography";

export default function TypographyList() {
  return (
    <Prose>
      <ul>
        <li>The channel the client writes in.</li>
        <li>The tracker that receives the issue.</li>
        <li>The project new issues belong to.</li>
      </ul>
    </Prose>
  );
}
