import { TurnFiles } from "@/components/ui/turn-files";
import type { ChatFile } from "@/lib/types";

const FILES: ChatFile[] = [
  {
    filename: "competitors-week-38.md",
    url: null,
    preview_url: null,
    media_type: "text/markdown",
    role: "details",
    subject: "Competitor changes, week of 14 September",
  },
];

export function FilesReportName() {
  return <TurnFiles files={FILES} onOpen={() => {}} />;
}
