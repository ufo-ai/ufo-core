import { AttachedFiles } from "@/components/ui/turn-files";
import type { ChatFile } from "@/lib/types";
import { SHOT_PAGE, SHOT_SIDEBAR } from "@/playground/examples/shots";

const FILES: ChatFile[] = [
  {
    filename: "release-note-2.14.pdf",
    url: "/dl/release-note-2.14.pdf",
    preview_url: SHOT_PAGE,
    media_type: "application/pdf",
    size_bytes: 223_232,
  },
  {
    filename: "sidebar-after.png",
    url: "/dl/sidebar-after.png",
    preview_url: SHOT_SIDEBAR,
    media_type: "image/png",
    size_bytes: 132_096,
  },
];

export function FilesAttached() {
  return <AttachedFiles files={FILES} picked={[]} onOpen={() => {}} />;
}
