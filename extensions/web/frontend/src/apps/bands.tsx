import { IconMessage } from "@tabler/icons-react";

import { PressRow } from "@/components/ui/pressrow";
import { Empty, Panel, Section, usePanelRead } from "@/kernel/panel";
import { slotOf } from "@/kernel/objects";
import { opened } from "@/kernel/slots";
import type { Placement } from "@/kernel/pager";
import { surfaceWord } from "@/lib/audience";
import { day } from "@/lib/moments";

/** How many rows of work a band draws. It is the newest of them, and the band says so where there
 *  are more — a listing that silently stopped at a page boundary would state a total nobody could
 *  reach and would order its rows by whatever the read happened to sort on. */
const SHOWN = 8;

type ConversationRow = { name: string; summary: string; surface: string; last_at: string };

type ConversationsPayload = { objects: ConversationRow[]; next_cursor: string | null };

/** What the app has done: the conversations it holds, its own runs among them.
 *
 *  It is the last band because it is the last question — what is this, what is it armed to do, what
 *  does it need from me, what has it been doing — and a member who has just met an app has no
 *  answer to read here yet, and it says so in a line rather than standing rows of made-up work in
 *  their place.
 *
 *  It draws the newest few and says so where there are more. The whole set is the conversations
 *  screen's answer, not this one: a band that paged would be a second listing with its own state
 *  in a page whose only channel for state is the place. */
export function AppConversations({
  agentId,
  title,
  blank,
  place,
  onPlace,
}: {
  agentId: string;
  title: string;
  blank: string;
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const state = usePanelRead<ConversationsPayload>(
    `/objects/conversation?agent=${agentId}&order_by=last_at&order=desc`,
  );
  const opens = place.opens ?? [];
  return (
    <Section title={title}>
      <Panel state={state} shape="table">
        {(payload) => {
          if (!payload.objects.length) {
            return <Empty className="my-3xl">{blank}</Empty>;
          }
          const newest = payload.objects.slice(0, SHOWN);
          return (
            <div className="flex flex-col">
              {newest.map((row) => {
                const lane = slotOf({ agent: agentId, kind: "conversation", name: row.name });
                return (
                  <PressRow
                    key={row.name}
                    glyph={<IconMessage className="size-(--size-glyph) shrink-0 text-ink-soft" />}
                    title={row.summary}
                    body={[surfaceWord(row.surface), day(row.last_at)]
                      .filter(Boolean)
                      .join(" · ")}
                    onPress={() => onPlace({ ...place, opens: opened(opens, lane) })}
                  />
                );
              })}
              {payload.objects.length > newest.length || payload.next_cursor ? (
                <p className="m-0 px-md py-lg text-label text-ink-soft">{MORE}</p>
              ) : null}
            </div>
          );
        }}
      </Panel>
    </Section>
  );
}

/** What the band says where it is showing the newest few of more. A listing that stopped at a page
 *  boundary in silence would read as the whole set. */
const MORE = "The newest few. The conversations screen holds the rest.";
