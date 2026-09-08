import { useEffect, useState } from "react";
import { RolloverRecord, get } from "./api";
import { Loading } from "./Loading";

export function Rollovers(props: { conversationId: string }) {
  const [indices, setIndices] = useState<number[] | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [record, setRecord] = useState<RolloverRecord | null>(null);

  useEffect(() => {
    setIndices(null);
    setOpen(null);
    setRecord(null);
    get<number[]>(`conversations/${props.conversationId}/rollovers`)
      .then(setIndices)
      .catch(() => setIndices([]));
  }, [props.conversationId]);

  useEffect(() => {
    if (open === null) return;
    setRecord(null);
    get<RolloverRecord>(`conversations/${props.conversationId}/rollovers/${open}`).then(
      setRecord,
    );
  }, [props.conversationId, open]);

  if (indices === null) return <Loading />;
  if (indices.length === 0) return <div className="empty">no rollovers</div>;
  return (
    <section className="panel">
      <h2>rollovers</h2>
      <div>
        {indices.map((index) => (
          <span
            key={index}
            className="chip rollover"
            onClick={() => setOpen(open === index ? null : index)}
          >
            rollover #{index}
          </span>
        ))}
      </div>
      {open !== null && record && (
        <>
          <dl className="kv">
            <dt>window</dt>
            <dd>
              {record.before.length} messages → {record.after.length}
            </dd>
            <dt>history</dt>
            <dd>
              <code>{record.recovery.history_path}</code> lines {record.recovery.first_entry_id}–
              {record.recovery.last_entry_id}
              {record.recovery.history_lost ? " (earlier history lost with the sandbox)" : ""}
            </dd>
            {record.recovery.objective && (
              <>
                <dt>objective (spawned turn)</dt>
                <dd>{record.recovery.objective}</dd>
              </>
            )}
            {record.recovery.handoff && (
              <>
                <dt>handoff</dt>
                <dd>{record.recovery.handoff}</dd>
              </>
            )}
            {record.recovery.checkpoint && (
              <>
                <dt>checkpoint (possibly stale)</dt>
                <dd>{record.recovery.checkpoint}</dd>
              </>
            )}
            {record.recovery.user_inputs.length > 0 && (
              <>
                <dt>member messages</dt>
                <dd>
                  {record.recovery.user_inputs.map((text, at) => (
                    <div key={at}>{text}</div>
                  ))}
                </dd>
              </>
            )}
            {record.recovery.pending_results.length > 0 && (
              <>
                <dt>unread results</dt>
                <dd>
                  {record.recovery.pending_results.map((result) => (
                    <div key={result.entry_id + result.call}>
                      <code>{result.call}</code> — entry {result.entry_id}
                      {result.truncated ? " (trimmed)" : ""}
                    </div>
                  ))}
                </dd>
              </>
            )}
            {record.recovery.checklist.length > 0 && (
              <>
                <dt>checklist</dt>
                <dd>{record.recovery.checklist.join(" · ")}</dd>
              </>
            )}
            {record.recovery.loaded_skills.length > 0 && (
              <>
                <dt>skills dropped</dt>
                <dd>{record.recovery.loaded_skills.join(" · ")}</dd>
              </>
            )}
          </dl>
          <details>
            <summary>before window (verbatim)</summary>
            <pre>{JSON.stringify(record.before, null, 2)}</pre>
          </details>
          <details>
            <summary>after window (what replaced it)</summary>
            <pre>{JSON.stringify(record.after, null, 2)}</pre>
          </details>
        </>
      )}
    </section>
  );
}
