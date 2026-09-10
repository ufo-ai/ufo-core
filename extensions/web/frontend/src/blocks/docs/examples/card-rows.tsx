import { Card, CardContent, CardHeader, CardTitle } from "@/blocks/card";

export default function CardRows() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Today</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="blk-card-row">
          <div className="blk-card-row-title">Design sync</div>
          <div className="blk-card-row-meta">2:30p • Google Meet</div>
        </div>
        <div className="blk-card-row">
          <div className="blk-card-row-title">Roadmap review</div>
          <div className="blk-card-row-meta">4:00p • Google Meet</div>
        </div>
        <div className="blk-card-row">
          <div className="blk-card-row-title">Hiring loop debrief</div>
          <div className="blk-card-row-meta">5:15p • Zoom</div>
        </div>
      </CardContent>
    </Card>
  );
}
