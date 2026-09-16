import { TurnFiles } from "@/components/ui/turn-files";
import type { ChatFile } from "@/lib/types";
import { SHOT_PAGE } from "@/playground/examples/shots";

const FILES: ChatFile[] = [
  {
    filename: "release-note-2.14.pdf",
    url: "/dl/release-note-2.14.pdf",
    preview_url: SHOT_PAGE,
    media_type: "application/pdf",
    size_bytes: 223_232,
  },
];

export function FilesDocument() {
  return <TurnFiles files={FILES} onOpen={() => {}} />;
}
