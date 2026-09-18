import { IconCheck, IconChevronRight, IconPlug } from "@tabler/icons-react";

import { Card } from "@/components/ui/card";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemMedia,
  ItemTitle,
  MarkTile,
  itemVariants,
} from "@/components/ui/item";
import { BASE } from "@/lib/api";
import { BrandMark } from "@/lib/brandMark";
import { cn } from "@/lib/cn";
import { ConsentLink } from "@/lib/consent";
import type { ChatConnect } from "@/lib/types";

const ROW = cn(
  itemVariants({ size: "row" }),
  "min-w-0 flex-1 text-inherit no-underline",
  "cursor-pointer border-0 bg-transparent text-start hover:bg-fill focus-visible:bg-fill",
);

/** The account a turn cannot go on without, and the same row once it has one: a card of the same
 *  make as the files a turn sends, so what a member is asked for reads like what they are given. */
export function ConnectLink({ connect }: { connect: ChatConnect }) {
  const named = connect.label ?? "account";
  const mark = (
    <ItemMedia>
      <MarkTile compact>
        {connect.provider ? (
          <BrandMark provider={connect.provider} className="size-(--size-glyph)" />
        ) : (
          <IconPlug aria-hidden className="size-(--size-glyph) shrink-0 text-ink-soft" />
        )}
      </MarkTile>
    </ItemMedia>
  );
  const inside = connect.turn ? (
    <>
      {mark}
      <ItemContent>
        <ItemTitle>{connect.label ? "Connect " + connect.label : "Connect account"}</ItemTitle>
        <ItemDescription>The turn waits on this.</ItemDescription>
      </ItemContent>
      <ItemActions>
        <IconChevronRight aria-hidden className="size-icon shrink-0 text-ink-soft" />
      </ItemActions>
    </>
  ) : (
    <>
      {mark}
      <ItemContent>
        <ItemTitle>{named + " connected"}</ItemTitle>
        {connect.account ? <ItemDescription>{connect.account}</ItemDescription> : null}
      </ItemContent>
      <ItemActions>
        <IconCheck aria-hidden className="size-icon shrink-0 text-ink" />
      </ItemActions>
    </>
  );
  return (
    <Card rows className="mt-sm max-w-said">
      <ItemGroup>
        <Item size="flush">
          {connect.turn ? (
            /* This surface mints the consent URL for the turn's request on press, so the row holds
               no address that can go stale. */
            <ConsentLink url={BASE + "/turns/" + connect.turn + "/connect"} className={ROW}>
              {inside}
            </ConsentLink>
          ) : (
            <span className={cn(ROW, "cursor-default hover:bg-transparent")}>{inside}</span>
          )}
        </Item>
      </ItemGroup>
    </Card>
  );
}
