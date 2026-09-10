import { useState } from "react";
import { IconChevronDown, IconDots, IconFilter2, IconSparkles, IconTargetArrow } from "@tabler/icons-react";

import {
  ActionBar,
  ActionBarActions,
  ActionBarTitle,
  IconButton,
  Prompt,
  Prompts,
  SearchField,
} from "@/blocks/action-bar";
import {
  Mark,
  ScoreDot,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableMeta,
  TableRow,
} from "@/blocks/table";
import "@/blocks/docs/compositions.css";

const LEADS = [
  { score: 98, company: "Stripe", category: "Payments", funding: "$35B Series G", headcount: "8,000" },
  { score: 96, company: "Airbnb", category: "Travel", funding: "$5.8B Series F", headcount: "6,000" },
  { score: 84, company: "Discord", category: "Social", funding: "$15B Series H", headcount: "2,500" },
  { score: 78, company: "Notion", category: "Productivity", funding: "$10B Series C", headcount: "1,000" },
  { score: 62, company: "Robinhood", category: "Finance", funding: "$11.7B Series G", headcount: "3,500" },
  { score: 58, company: "Instacart", category: "Grocery", funding: "$14B Series F", headcount: "3,000" },
  { score: 54, company: "Chime", category: "Banking", funding: "$25B Series F", headcount: "1,500" },
  { score: 48, company: "Plaid", category: "Fintech", funding: "$5.3B Series D", headcount: "1,200" },
  { score: 42, company: "Brex", category: "Finance", funding: "$12.3B Series C", headcount: "1,000" },
  { score: 38, company: "Ramp", category: "Finance", funding: "$8.1B Series C", headcount: "1,500" },
  { score: 32, company: "Figma", category: "Design", funding: "$10B Series D", headcount: "1,000" },
  { score: 28, company: "Clubhouse", category: "Social", funding: "$4B Series C", headcount: "100" },
];

export default function LeadsLane() {
  const [query, setQuery] = useState("");
  const term = query.trim().toLowerCase();
  const rows = LEADS.filter((lead) => lead.company.toLowerCase().includes(term));
  return (
    <div className="blk-lane" data-width="wide">
      <ActionBar>
        <ActionBarTitle icon={<IconTargetArrow size={16} stroke={1.5} />}>Leads</ActionBarTitle>
        <ActionBarActions>
          <IconButton label="More">
            <IconDots size={16} stroke={1.5} />
          </IconButton>
        </ActionBarActions>
      </ActionBar>
      <ActionBar variant="toolbar">
        <Prompts>
          <Prompt icon={<IconSparkles size={16} stroke={1.5} />}>Summarize</Prompt>
          <Prompt icon={<IconSparkles size={16} stroke={1.5} />}>Brainstorm</Prompt>
          <Prompt icon={<IconSparkles size={16} stroke={1.5} />}>Delegate</Prompt>
        </Prompts>
        <ActionBarActions>
          <IconButton label="Filter leads">
            <IconFilter2 size={16} stroke={1.5} />
          </IconButton>
          <SearchField value={query} onChange={setQuery} />
        </ActionBarActions>
      </ActionBar>
      <div className="blk-lane-body">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead width={16}>
                <IconChevronDown size={16} stroke={1.5} />
              </TableHead>
              <TableHead width={55}>Fit Score</TableHead>
              <TableHead>Company</TableHead>
              <TableHead>Category</TableHead>
              <TableHead>Funding</TableHead>
              <TableHead>Headcount</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((lead, index) => (
              <TableRow key={lead.company}>
                <TableCell>
                  <TableMeta>{index + 1}</TableMeta>
                </TableCell>
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
      </div>
    </div>
  );
}
