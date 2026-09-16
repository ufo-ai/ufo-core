import { TurnFiles } from "@/components/ui/turn-files";
import type { ChatFile } from "@/lib/types";

const FILES: ChatFile[] = [
  {
    filename: "support-macros.xlsx",
    url: "/dl/support-macros.xlsx",
    preview_url: null,
    media_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    size_bytes: 65_536,
  },
];

export function FilesDocumentPlain() {
  return <TurnFiles files={FILES} onOpen={() => {}} />;
}
