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
import { Input } from "@/components/ui/field";
import { Filter } from "@/components/ui/filter";
import { Td } from "@/components/ui/table";
import { Pager, type Placement } from "@/kernel/pager";
import { PageToolbar } from "@/kernel/pane";
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
import { Moment } from "@/lib/moments";
import { postIntent } from "@/lib/api";
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

  return (
    <>
      {!submitted && kinds.length ? (
        <PageToolbar>
          <Filter
            options={kinds.map((kind) => ({ label: kindLabel(kind), value: kind }))}
            value={place.kind ?? ""}
            onChange={(kind) => onPlace({ kind: kind || undefined, after: undefined })}
          />
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

function CorrectionDialog({ match, onSuccess }: { match: Match; onSuccess: () => void }) {
  const mainAgent = useMainAgent();
  const [body, setBody] = useState(match.text);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<NoticeState>(QUIET);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !body.trim() || !mainAgent || !match.ref) return;
    setBusy(true);
    const outcome = await postIntent(mainAgent.id, {
      verb: "record",
      kind: "memory",
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
        <DialogDescription>The correction replaces this memory.</DialogDescription>
      </DialogHeader>
      <OutcomeNotice state={notice} />
      <form id="correct-memory" onSubmit={submit} className="flex flex-col items-start gap-sm">
        <Input
          autoFocus
          aria-label="Memory"
          value={body}
          onChange={(event) => setBody(event.target.value)}
          className="max-w-none w-full"
        />
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
