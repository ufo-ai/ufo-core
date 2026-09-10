import { IconChevronDown } from "@tabler/icons-react";

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

const LEADS = [
  { score: 98, company: "Stripe", category: "Payments", funding: "$35B Series G", headcount: "8,000" },
  { score: 96, company: "Airbnb", category: "Travel", funding: "$5.8B Series F", headcount: "6,000" },
  { score: 84, company: "Discord", category: "Social", funding: "$15B Series H", headcount: "2,500" },
  { score: 78, company: "Notion", category: "Productivity", funding: "$10B Series C", headcount: "1,000" },
  { score: 62, company: "Robinhood", category: "Finance", funding: "$11.7B Series G", headcount: "3,500" },
  { score: 48, company: "Plaid", category: "Fintech", funding: "$5.3B Series D", headcount: "1,200" },
  { score: 28, company: "Clubhouse", category: "Social", funding: "$4B Series C", headcount: "100" },
  { score: 12, company: "Zoom", category: "Video", funding: "$35B IPO", headcount: "6,000" },
];

export function TableLeads() {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead width={32}>
            <IconChevronDown size={16} stroke={1.5} />
          </TableHead>
          <TableHead width={71}>Fit Score</TableHead>
          <TableHead>Company</TableHead>
          <TableHead>Category</TableHead>
          <TableHead>Funding</TableHead>
          <TableHead>Headcount</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {LEADS.map((lead, index) => (
          <TableRow key={lead.company}>
            <TableCell muted="dim">{index + 1}</TableCell>
            <TableCell>
              <ScoreDot value={lead.score} />
            </TableCell>
            <TableCell>
              <Mark />
              {lead.company}
            </TableCell>
            <TableCell muted>{lead.category}</TableCell>
            <TableCell muted>{lead.funding}</TableCell>
            <TableCell muted>{lead.headcount}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
