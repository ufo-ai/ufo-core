import {
  Card,
  CardButton,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/blocks/card";

export default function CardLarge() {
  return (
    <Card size="lg">
      <CardHeader>
        <CardTitle size="lg">Q2 dividend income</CardTitle>
        <CardDescription>Payouts from the twelve holdings that reported this quarter.</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="blk-card-row">
          <div className="blk-card-row-title">Northwind Energy</div>
          <div className="blk-card-row-meta">450 shares • $1,240</div>
        </div>
        <div className="blk-card-row">
          <div className="blk-card-row-title">Halcyon Materials</div>
          <div className="blk-card-row-meta">120 shares • $386</div>
        </div>
      </CardContent>
      <CardFooter>
        <CardButton variant="primary" size="lg">
          View full report
        </CardButton>
      </CardFooter>
    </Card>
  );
}
