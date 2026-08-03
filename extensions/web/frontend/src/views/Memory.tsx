import { useState, type FormEvent } from "react";

import { Pager, type Placement } from "@/kernel/pager";
import { day } from "@/views/Tasks";
import { Button } from "@/components/ui/button";
import { Table, Td, Th } from "@/components/ui/table";
import { type NoticeState, OutcomeNotice, Panel, PanelEmpty, QUIET, usePanelRead } from "@/kernel/panel";
import { postIntent } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useMainAgent } from "@/lib/mainAgent";

type Match = {
  text: string;
  kind: string;
  ref: string | null;
  created_at: string | null;
};

type MemoryPayload = {
  available: boolean;
  kinds: string[];
  matches: Match[];
  newer?: string | null;
  older?: string | null;
};

export function Memory({
  place,
  onPlace,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [reloads, setReloads] = useState(0);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
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

  function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitted(query.trim());
    setCorrecting(null);
  }

  return (
    <>
      <form onSubmit={submit} className="mb-lg flex gap-sm">
        <input
          placeholder="Search memory…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="flex-1 rounded-panel border border-edge-control bg-field px-md py-sm text-field-ink"
        />
        <Button type="submit" variant="send">
          Search
        </Button>
      </form>
      <Panel
        state={state}
        empty={(payload) => (payload.available ? null : "This deploy has no memory extension.")}
      >
        {(payload) => (
          <>
            {submitted ? null : (
              <div className="mb-lg flex gap-xs">
                {["all", ...payload.kinds].map((kind) => {
                  const chosen = kind === "all" ? !place.kind : place.kind === kind;
                  return (
                    <button
                      key={kind}
                      type="button"
                      aria-current={chosen}
                      onClick={() => onPlace({ kind: kind === "all" ? undefined : kind })}
                      className={cn(
                        "border border-edge-control rounded-control bg-transparent px-sm py-hair text-inherit",
                        chosen && "font-strong underline",
                      )}
                    >
                      {kind}
                    </button>
                  );
                })}
              </div>
            )}
            {!payload.matches.length ? (
              <>
                <PanelEmpty>
                  {submitted
                    ? "No matches."
                    : place.kind
                      ? "No " + place.kind + " memories on this page."
                      : "No memories yet."}
                </PanelEmpty>
                {submitted ? null : (
                  <Pager payload={payload} place={place} onPlace={onPlace} />
                )}
              </>
            ) : (
              <>
                <Table>
                  <thead>
                    <tr>
                      {["memory", "kind", "ref", "date", ""].map((column, index) => (
                        <Th key={index}>{column}</Th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {payload.matches.map((match, index) => (
                      <tr key={index}>
                        <Td>{match.text}</Td>
                        <Td>{match.kind}</Td>
                        <Td>{match.ref ?? "—"}</Td>
                        <Td>{day(match.created_at) ?? "—"}</Td>
                        <Td>
                          {typeof match.ref === "string" && match.ref.startsWith("memory/") ? (
                            <Button variant="row" onClick={() => setCorrecting(match)}>
                              Correct
                            </Button>
                          ) : (
                            "—"
                          )}
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
                {submitted ? null : (
                  <Pager payload={payload} place={place} onPlace={onPlace} />
                )}
              </>
            )}
          </>
        )}
      </Panel>
      {correcting ? (
        <CorrectionForm
          match={correcting}
          onDone={(message) => {
            setCorrecting(null);
            if (message === null) setReloads((count) => count + 1);
            else setNotice({ text: message, refused: true });
          }}
        />
      ) : null}
      <OutcomeNotice state={notice} />
    </>
  );
}

function CorrectionForm({
  match,
  onDone,
}: {
  match: Match;
  onDone: (message: string | null) => void;
}) {
  const mainAgent = useMainAgent();
  const [body, setBody] = useState(match.text);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!body.trim() || !mainAgent || !match.ref) return;
    setBusy(true);
    const outcome = await postIntent(mainAgent.id, {
      verb: "record",
      kind: "memory",
      corrects: match.ref.slice("memory/".length),
      body: body.trim(),
    });
    setBusy(false);
    onDone(outcome.applied ? null : outcome.message);
  }

  return (
    <form onSubmit={submit} className="my-lg flex gap-sm">
      <input
        autoFocus
        value={body}
        onChange={(event) => setBody(event.target.value)}
        className="flex-1 rounded-panel border border-edge-control bg-field px-md py-sm text-field-ink"
      />
      <Button type="submit" variant="send" disabled={busy}>
        Record correction
      </Button>
    </form>
  );
}
