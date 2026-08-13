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
import { Table, TableNote, Td, Th } from "@/components/ui/table";
import { Pager, type Placement } from "@/kernel/pager";
import {
  OutcomeNotice,
  Panel,
  PanelBlank,
  PanelEmpty,
  QUIET,
  Section,
  usePanelRead,
  type NoticeState,
} from "@/kernel/panel";
import { day } from "@/lib/moments";
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
  const [query, setQuery] = useState(place.q ?? "");
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

  useEffect(() => setQuery(place.q ?? ""), [place.q]);

  function submit(event: FormEvent) {
    event.preventDefault();
    onPlace({ q: query.trim() || undefined, after: undefined });
    setCorrecting(null);
  }

  const kinds = state.phase === "ready" ? state.payload.kinds : [];
  const columns = ["Memory", "Class", "Audience", "Added"];

  return (
    <>
      <Section
        title="Memory"
        bar={
          <>
            <form onSubmit={submit} className="flex items-stretch">
              <Input
                type="search"
                aria-label="Search"
                placeholder="Search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                className="max-w-control-row"
              />
            </form>
            {!submitted && kinds.length ? (
              <Filter
                options={kinds.map((kind) => ({
                  label: kindLabel(kind),
                  value: kind,
                }))}
                value={place.kind ?? ""}
                onChange={(kind) =>
                  onPlace({ kind: kind || undefined, after: undefined })
                }
              />
            ) : null}
          </>
        }
      >
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
            const narrowed =
              Boolean(submitted) || (Boolean(place.kind) && !payload.matches.length);
            if (!payload.matches.length && !narrowed)
              return (
                <PanelBlank body="No memories yet." />
              );
            const corrections = payload.matches.some(correctable);
            return (
              <>
                <Table>
                  <thead>
                    <tr>
                      {columns.map((column) => (
                        <Th key={column}>{column}</Th>
                      ))}
                      {corrections ? <Th>{""}</Th> : null}
                    </tr>
                  </thead>
                  <tbody>
                    {payload.matches.length ? (
                      payload.matches.map((match, index) => (
                        <tr key={index}>
                          <Td className="w-full">{match.text}</Td>
                          <Td className="whitespace-nowrap">{kindLabel(match.kind)}</Td>
                          <Td className="whitespace-nowrap">{subjectLabel(match.subject)}</Td>
                          <Td className="whitespace-nowrap opacity-(--opacity-muted)">
                            {day(match.created_at) ?? "—"}
                          </Td>
                          {corrections ? (
                            <Td>
                              {correctable(match) ? (
                                <Button variant="row" onClick={() => setCorrecting(match)}>
                                  Correct
                                </Button>
                              ) : null}
                            </Td>
                          ) : null}
                        </tr>
                      ))
                    ) : (
                      <TableNote span={columns.length}>
                        {submitted
                          ? "No matches."
                          : place.kind && !payload.kinds.includes(place.kind)
                            ? "That memory class is not available."
                            : "No memories of this kind on this page."}
                      </TableNote>
                    )}
                  </tbody>
                </Table>
                {submitted ? null : <Pager payload={payload} onPlace={onPlace} />}
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
