import { TurnFiles } from "@/components/ui/turn-files";
import type { ChatFile } from "@/lib/types";
import { SHOT_REVENUE, SHOT_SIDEBAR, SHOT_TREND } from "@/playground/examples/shots";

const FILES: ChatFile[] = [
  {
    filename: "revenue-by-week.png",
    url: "/dl/revenue-by-week.png",
    preview_url: SHOT_REVENUE,
    media_type: "image/png",
    size_bytes: 184_320,
  },
  {
    filename: "signups-since-june.png",
    url: "/dl/signups-since-june.png",
    preview_url: SHOT_TREND,
    media_type: "image/png",
    size_bytes: 96_256,
  },
  {
    filename: "sidebar-after.png",
    url: "/dl/sidebar-after.png",
    preview_url: SHOT_SIDEBAR,
    media_type: "image/png",
    size_bytes: 132_096,
  },
];

export function FilesPictures() {
  return <TurnFiles files={FILES} onOpen={() => {}} />;
}
