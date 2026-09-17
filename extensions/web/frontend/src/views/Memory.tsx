import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Sheet } from "@/components/ui/sheet";
import { Td, TdFill } from "@/components/ui/table";
import { ActionForm } from "@/kernel/action";
import { type Placement } from "@/kernel/pager";
import { PageToolbar, usePageSearch } from "@/kernel/pane";
import { Panel, PanelEmpty, Section, usePanelRead } from "@/kernel/panel";
import { DataTable, type Column } from "@/kernel/table";
import { cn } from "@/lib/cn";
import { Moment } from "@/lib/moments";
import { postAction } from "@/lib/api";
import { ShareMark } from "@/lib/chatMark";
import { useMainAgent } from "@/lib/mainAgent";
import type { ActionView } from "@/lib/types";

type Match = {
  text: string;
  kind: string;
  ref: string | null;
  created_at: string | null;
  subject: string | null;
};

type MemoryPayload = {
  available: boolean;
  matches: Match[];
  actions: ActionView[];
  newer?: string | null;
  older?: string | null;
};

const RECORD_CORRECTION_ACTION = "record_correction";

const MEMORY_REF_PREFIX = "memory/";

const COLUMNS: Column[] = [
  { label: "Memory", fill: true },
  { label: "Added", fact: true },
];

/** The statement is what the member came to read; the stamp beside it places it and recedes. */
const FACT = "text-ink";

function correctable(match: Match) {
  return typeof match.ref === "string" && match.ref.startsWith("memory/");
}

export function Memory({
  place,
  onPlace,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const submitted = place.q ?? "";
  const [reloads, setReloads] = useState(0);
  const [correcting, setCorrecting] = useState<Match | null>(null);

  const params = new URLSearchParams();
  if (submitted) params.set("q", submitted);
  else if (place.after) params.set("after", place.after);
  const search = params.toString();
  const state = usePanelRead<MemoryPayload>(
    "/workspace/memory" + (search ? "?" + search : ""),
    reloads,
  );
  const update =
    state.phase === "ready"
      ? state.payload.actions.find((view) => view.name === RECORD_CORRECTION_ACTION)
      : undefined;

  useEffect(() => setCorrecting(null), [submitted]);

  const box = usePageSearch();

  return (
    <>
      {box ? <PageToolbar /> : null}
      <Section>
        <Panel
          state={state}
          empty={(payload) => (payload.available ? null : "This deploy has no memory extension.")}
          failed={
            place.after
              ? (message) => (
                  <>
                    <PanelEmpty>{message}</PanelEmpty>
                    <div className="mb-lg flex gap-xs">
                      <Button variant="row" onClick={() => onPlace({ after: undefined })}>
                        First page
                      </Button>
                    </div>
                  </>
                )
              : undefined
          }
        >
          {(payload) => {
            const note = submitted ? "No matches." : undefined;
            return (
              <DataTable
                columns={COLUMNS}
                rows={payload.matches}
                rowKey={(match) => match.ref ?? match.text}
                lede
                empty="No memories yet."
                note={note}
                open={(match) => (correctable(match) ? () => setCorrecting(match) : null)}
                pager={submitted ? undefined : { payload, after: place.after, onPlace }}
              >
                {(match) => (
                  <>
                    <TdFill className={FACT}>
                      <span className="flex min-w-0 items-center gap-sm">
                        <span className="truncate">{match.text}</span>
                        <span className="ml-auto flex shrink-0 items-center">
                          <ShareMark subject={match.subject} />
                        </span>
                      </span>
                    </TdFill>
                    <Td>{match.created_at ? <Moment at={match.created_at} /> : "—"}</Td>
                  </>
                )}
              </DataTable>
            );
          }}
        </Panel>
      </Section>
      {correcting && update ? (
        <Sheet
          open
          title="Edit memory"
          onClose={() => setCorrecting(null)}
          describedBy="memory-correction-description"
        >
          <CorrectionSheet
            match={correcting}
            view={update}
            onSuccess={() => {
              setCorrecting(null);
              setReloads((count) => count + 1);
            }}
          />
        </Sheet>
      ) : null}
    </>
  );
}

function CorrectionSheet({
  match,
  view,
  onSuccess,
}: {
  match: Match;
  view: ActionView;
  onSuccess: () => void;
}) {
  const mainAgent = useMainAgent();
  const limit = view.input_schema.properties?.body?.maxLength;
  const overlong = limit !== undefined && match.text.length > limit;

  return (
    <>
      <p id="memory-correction-description" className="m-0 text-label text-ink-soft">
        {overlong
          ? "This memory is too long to edit. Write the new statement."
          : "The edit replaces this memory."}
      </p>
      {overlong ? (
        <p
          className={cn(
            "w-full rounded-panel bg-fill px-lg py-md",
            "text-subtitle narrow:text-ui text-ink-soft",
          )}
        >
          {match.text}
        </p>
      ) : null}
      <ActionForm
        view={view}
        initial={{ body: overlong ? "" : match.text }}
        fixed={{ corrects: (match.ref ?? "").slice(MEMORY_REF_PREFIX.length) }}
        act={async (input) => {
          if (!mainAgent) return { applied: false, message: "" };
          const outcome = await postAction(mainAgent.id, view.call, input);
          if (outcome.applied) onSuccess();
          return outcome;
        }}
      />
    </>
  );
}
