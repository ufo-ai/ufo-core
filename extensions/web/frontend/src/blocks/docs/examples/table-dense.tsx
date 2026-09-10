import { ScoreDot, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/blocks/table";

const ROWS = [
  { score: 98, company: "Stripe", funding: "$35B Series G" },
  { score: 62, company: "Robinhood", funding: "$11.7B Series G" },
  { score: 48, company: "Plaid", funding: "$5.3B Series D" },
  { score: 28, company: "Clubhouse", funding: "$4B Series C" },
  { score: 12, company: "Zoom", funding: "$35B IPO" },
];

export function TableDense() {
  return (
    <Table density="dense">
      <TableHeader>
        <TableRow>
          <TableHead width={71}>Fit Score</TableHead>
          <TableHead>Company</TableHead>
          <TableHead>Funding</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {ROWS.map((row) => (
          <TableRow key={row.company}>
            <TableCell>
              <ScoreDot value={row.score} />
            </TableCell>
            <TableCell>{row.company}</TableCell>
            <TableCell muted>{row.funding}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
