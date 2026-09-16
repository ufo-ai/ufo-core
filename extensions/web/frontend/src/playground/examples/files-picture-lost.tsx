import { TurnFiles } from "@/components/ui/turn-files";
import type { ChatFile } from "@/lib/types";

const LOST = "data:image/png;base64,";

const FILES: ChatFile[] = [
  {
    filename: "dashboard-export.png",
    url: "/dl/dashboard-export.png",
    preview_url: LOST,
    media_type: "image/png",
    size_bytes: 248_832,
  },
];

export function FilesPictureLost() {
  return <TurnFiles files={FILES} onOpen={() => {}} />;
}
