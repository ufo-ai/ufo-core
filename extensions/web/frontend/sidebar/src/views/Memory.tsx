import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Hint, Input } from "@/components/ui/field";
import { Filter } from "@/components/ui/filter";
import { Td } from "@/components/ui/table";
import { Pager, type Placement } from "@/kernel/pager";
import { PageToolbar, usePageSearch } from "@/kernel/pane";
import {
  OutcomeNotice,
  Panel,
  PanelEmpty,
  QUIET,
  Section,
  usePanelRead,
  type NoticeState,
} from "@/kernel/panel";
import { DataTable, type Column } from "@/kernel/table";
import { cn } from "@/lib/cn";
import { Moment } from "@/lib/moments";
import { postKindAction } from "@/lib/api";
import { subjectLabel } from "@/lib/audience";
import { useMainAgent } from "@/lib/mainAgent";

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
  /** How long a body the installed memory provider stores, stated by the read rather than held as
   *  a number here: the tool refuses past it, so the form has to stop the member at the same
   *  length, and a copy kept in the portal would drift the day the provider moves its own. */
  body_max_chars?: number;
  newer?: string | null;
  older?: string | null;
};

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
                  act={(match) => (correctable(match) ? "Correct" : null)}
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
      <Dialog open={correcting !== null} onOpenChange={(next) => !next && setCorrecting(null)}>
        {correcting ? (
          <CorrectionDialog
            match={correcting}
            limit={state.phase === "ready" ? state.payload.body_max_chars : undefined}
            onSuccess={() => {
              setCorrecting(null);
              setReloads((count) => count + 1);
            }}
          />
        ) : null}
      </Dialog>
    </>
  );
}

/** One memory restated. `limit` is the provider's own body bound, carried by the read that drew
 *  this row: the field stops there and says how much is left, so the member meets the rule while
 *  they write rather than as a refusal after they submit. A correction is a new statement and
 *  never an edit of the row it names, so a row past the bound — the Overview paragraph is one —
 *  opens the field empty with its current text above it to write against, rather than seeding a
 *  body the tool would refuse and a count that has already run out. */
function CorrectionDialog({
  match,
  limit,
  onSuccess,
}: {
  match: Match;
  limit?: number;
  onSuccess: () => void;
}) {
  const mainAgent = useMainAgent();
  const overlong = limit !== undefined && match.text.length > limit;
  const [body, setBody] = useState(overlong ? "" : match.text);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<NoticeState>(QUIET);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !body.trim() || !mainAgent || !match.ref) return;
    setBusy(true);
    const outcome = await postKindAction(mainAgent.id, "memory", "record_correction", {
      corrects: match.ref.slice("memory/".length),
      body: body.trim(),
    });
    setBusy(false);
    if (outcome.applied) onSuccess();
    else setNotice({ text: outcome.message, refused: true });
  }

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Correct Memory</DialogTitle>
        <DialogDescription>
          {overlong
            ? "This memory is longer than a correction may run. Write the corrected statement."
            : "The correction replaces this memory."}
        </DialogDescription>
      </DialogHeader>
      <OutcomeNotice state={notice} />
      <form id="correct-memory" onSubmit={submit} className="flex flex-col items-start gap-sm">
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
        <Input
          autoFocus
          aria-label="Memory"
          maxLength={limit}
          value={body}
          onChange={(event) => setBody(event.target.value)}
          className="max-w-none w-full"
        />
        {limit === undefined ? null : (
          <Hint className="m-0">{limit - body.length} characters left</Hint>
        )}
      </form>
      <DialogFooter>
        <Button
          type="submit"
          form="correct-memory"
          variant="send"
          busy={busy}
          disabled={!body.trim()}
        >
          Record correction
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}
