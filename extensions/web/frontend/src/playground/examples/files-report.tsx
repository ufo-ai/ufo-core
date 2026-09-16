import { TurnFiles } from "@/components/ui/turn-files";
import type { ChatFile } from "@/lib/types";

const FILES: ChatFile[] = [
  {
    filename: "competitors-week-38.md",
    url: "/dl/competitors-week-38.md",
    preview_url: null,
    media_type: "text/markdown",
    role: "details",
    subject: "Competitor changes, week of 14 September",
  },
];

export function FilesReport() {
  return <TurnFiles files={FILES} onOpen={() => {}} />;
}
