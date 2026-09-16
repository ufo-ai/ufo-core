import { TurnFiles } from "@/components/ui/turn-files";
import type { ChatFile } from "@/lib/types";
import { SHOT_REVENUE } from "@/playground/examples/shots";

const FILES: ChatFile[] = [
  {
    filename: "revenue-by-week.png",
    url: "/dl/revenue-by-week.png",
    preview_url: SHOT_REVENUE,
    media_type: "image/png",
    size_bytes: 184_320,
  },
];

export function FilesPicture() {
  return <TurnFiles files={FILES} onOpen={() => {}} />;
}
