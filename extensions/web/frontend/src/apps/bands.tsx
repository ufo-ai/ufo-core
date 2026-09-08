import { IconMessage } from "@tabler/icons-react";

import { PressRow } from "@/components/ui/pressrow";
import { Empty, Panel, Section, usePanelRead } from "@/kernel/panel";
import { slotOf } from "@/kernel/objects";
import { opened } from "@/kernel/slots";
import type { Placement } from "@/kernel/pager";
import { surfaceWord } from "@/lib/audience";
import { day } from "@/lib/moments";

const SHOWN = 8;

type ConversationRow = { name: string; summary: string; surface: string; last_at: string };

type ConversationsPayload = { objects: ConversationRow[]; next_cursor: string | null };

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
                    line={row.summary}
                    note={[surfaceWord(row.surface), day(row.last_at)]
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

const MORE = "The newest few. The conversations screen holds the rest.";
