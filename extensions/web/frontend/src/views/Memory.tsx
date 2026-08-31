import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Filter } from "@/components/ui/filter";
import { Sheet } from "@/components/ui/sheet";
import { Td } from "@/components/ui/table";
import { ActionForm } from "@/kernel/action";
import { Pager, type Placement } from "@/kernel/pager";
import { PageToolbar, usePageSearch } from "@/kernel/pane";
import { Panel, PanelEmpty, Section, usePanelRead } from "@/kernel/panel";
import { DataTable, type Column } from "@/kernel/table";
import { cn } from "@/lib/cn";
import { Moment } from "@/lib/moments";
import { postAction } from "@/lib/api";
import { subjectLabel } from "@/lib/audience";
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
  kinds: string[];
  matches: Match[];
  /** The acts the memory collection projects for this reader — the correction's write among them,
   *  whose own schema bounds the body a correction may run to. */
  actions: ActionView[];
  newer?: string | null;
  older?: string | null;
};

const RECORD_CORRECTION_ACTION = "record_correction";

/** How a search hit names its row: the kind and the item's id, which is what a correction names. */
const MEMORY_REF_PREFIX = "memory/";

const COLUMNS: Column[] = [
  "Memory",
  { label: "Class", fact: true },
  { label: "Audience", fact: true },
  { label: "Added", fact: true },
];

function kindLabel(kind: string) {
  return kind.charAt(0).toUpperCase() + kind.slice(1);
}

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
  else {
    if (place.kind) params.set("kind", place.kind);
    if (place.after) params.set("after", place.after);
  }
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

  const kinds = state.phase === "ready" ? state.payload.kinds : [];
  // A search is over every memory the workspace holds, so it supersedes the class the member had
  // narrowed to and that filter stands down while a term is submitted. The box itself does not: it
  // is what the member clears the term with, and a bar that took it away with the filter would
  // leave the search they typed with no way back out of it.
  const box = usePageSearch();
  const narrowing = !submitted && kinds.length > 0;

  return (
    <>
      {box || narrowing ? (
        <PageToolbar>
          {narrowing ? (
            <Filter
              options={kinds.map((kind) => ({ label: kindLabel(kind), value: kind }))}
              value={place.kind ?? ""}
              onChange={(kind) => onPlace({ kind: kind || undefined, after: undefined })}
            />
          ) : null}
        </PageToolbar>
      ) : null}
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
            const note = submitted
              ? "No matches."
              : place.kind
                ? payload.kinds.includes(place.kind)
                  ? "No memories of this kind on this page."
                  : "That memory class is not available."
                : undefined;
            const listed = Boolean(payload.matches.length || note);
            return (
              <>
                <DataTable
                  columns={COLUMNS}
                  rows={payload.matches}
                  rowKey={(match) => match.ref ?? match.text}
                  empty="No memories yet."
                  note={note}
                  open={(match) => (correctable(match) ? () => setCorrecting(match) : null)}
                  act={(match) => (correctable(match) ? "Edit" : null)}
                >
                  {(match) => (
                    <>
                      <Td>{match.text}</Td>
                      <Td>{kindLabel(match.kind)}</Td>
                      <Td>{subjectLabel(match.subject)}</Td>
                      <Td>{match.created_at ? <Moment at={match.created_at} /> : "—"}</Td>
                    </>
                  )}
                </DataTable>
                {submitted || !listed ? null : <Pager payload={payload} onPlace={onPlace} />}
              </>
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

/** One memory restated through the memory collection's projected correction: the body is the one
 *  field the member types, bounded by the action's own schema, and the corrected item rides
 *  `corrects` pinned. A correction is a new statement and never an edit of the row it names, so a row past the
 *  bound — the Overview paragraph is one — opens the field empty with its current text above it to
 *  write against, rather than seeding a body the action would refuse. */
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
