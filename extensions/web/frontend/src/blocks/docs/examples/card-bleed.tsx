import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/blocks/card";

const STRIP =
  "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='640' height='120'><rect width='640' height='120' fill='%23ff6700'/><rect x='360' width='280' height='120' fill='%230095ff'/></svg>";

export default function CardBleed() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Usage</CardTitle>
        <CardDescription>Requests against the plan, week by week.</CardDescription>
      </CardHeader>
      <CardContent bleed>
        <img src={STRIP} alt="" style={{ display: "block", width: "100%", height: 120, objectFit: "cover" }} />
      </CardContent>
      <CardContent>
        <p>Bleed pulls the content out to the card edge by the card's own spacing.</p>
      </CardContent>
    </Card>
  );
}
