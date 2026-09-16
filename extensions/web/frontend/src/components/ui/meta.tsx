import type { ReactNode } from "react";

import { CopyAct } from "@/components/ui/copy-act";
import { Marker, MarkerContent } from "@/components/ui/marker";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { BrandMark } from "@/lib/brandMark";
import { modelLabel, modelMark } from "@/lib/models";

/** The turn's spend, with its model standing as the mark alone. The picker's name for that model
 *  reaches the member on hover and on focus. The mark is sized in `em` so it stands to the meta
 *  line's own type rather than to the glyph size a marker gives an icon.
 *
 *  Only the last message in a transcript carries its line openly. An earlier one draws it on hover
 *  and on keyboard focus, so a long thread reads as speech rather than as a ledger. The line keeps
 *  its space either way: revealing it must not move the text above it. */
export function Meta({
  model,
  last = true,
  mine = false,
  copy,
  children,
}: {
  model?: string | null;
  last?: boolean;
  mine?: boolean;
  copy?: string;
  children: ReactNode;
}) {
  const mark = model ? modelMark(model) : null;
  return (
    <Marker
      variant="stamp"
      align={mine ? "end" : "start"}
      reveal={!last}
      className="mt-2xs gap-md"
    >
      {mark && model ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <span tabIndex={0} className="flex shrink-0 items-center">
              <BrandMark provider={mark} className="size-icon" />
              <span className="sr-only">{modelLabel(model)}</span>
            </span>
          </TooltipTrigger>
          <TooltipContent side="top">{modelLabel(model)}</TooltipContent>
        </Tooltip>
      ) : null}
      {copy ? <CopyAct text={copy} /> : null}
      <MarkerContent className="flex items-center gap-md">{children}</MarkerContent>
    </Marker>
  );
}
