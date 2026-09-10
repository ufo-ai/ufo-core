import { Card, CardDescription, CardHeader, CardTitle, CardContent } from "@/blocks/card";
import {
  Mark,
  ScoreDot,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/blocks/table";

const ROWS = [
  { score: 98, company: "Stripe", funding: "$35B Series G" },
  { score: 84, company: "Discord", funding: "$15B Series H" },
  { score: 62, company: "Robinhood", funding: "$11.7B Series G" },
  { score: 48, company: "Plaid", funding: "$5.3B Series D" },
  { score: 28, company: "Clubhouse", funding: "$4B Series C" },
];

export default function CardWithTable() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Top leads</CardTitle>
        <CardDescription>The five accounts that scored highest this week.</CardDescription>
      </CardHeader>
      <CardContent>
        <Table density="dense">
          <TableHeader>
            <TableRow>
              <TableHead width={55}>Fit Score</TableHead>
              <TableHead>Company</TableHead>
              <TableHead align="right">Funding</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {ROWS.map((row) => (
              <TableRow key={row.company}>
                <TableCell>
                  <ScoreDot value={row.score} />
                </TableCell>
                <TableCell>
                  <Mark />
                  {row.company}
                </TableCell>
                <TableCell align="right" muted>
                  {row.funding}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
