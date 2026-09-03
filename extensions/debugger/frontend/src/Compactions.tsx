import { useEffect, useState } from "react";
import { CompactionRecord, get } from "./api";
import { Loading } from "./Loading";

export function Compactions(props: { conversationId: string }) {
  const [indices, setIndices] = useState<number[] | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [record, setRecord] = useState<CompactionRecord | null>(null);

  useEffect(() => {
    setIndices(null);
    setOpen(null);
    setRecord(null);
    get<number[]>(`conversations/${props.conversationId}/compactions`)
      .then(setIndices)
      .catch(() => setIndices([]));
  }, [props.conversationId]);

  useEffect(() => {
    if (open === null) return;
    setRecord(null);
    get<CompactionRecord>(`conversations/${props.conversationId}/compactions/${open}`).then(
      setRecord,
    );
  }, [props.conversationId, open]);

  if (indices === null) return <Loading />;
  if (indices.length === 0) return <div className="empty">no compactions</div>;
  return (
    <section className="panel">
      <h2>compactions</h2>
      <div>
        {indices.map((index) => (
          <span
            key={index}
            className="chip compaction"
            onClick={() => setOpen(open === index ? null : index)}
          >
            compaction #{index}
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
            <dt>intent</dt>
            <dd>{record.summary.intent}</dd>
            <dt>current work</dt>
            <dd>{record.summary.current_work}</dd>
            <dt>next step</dt>
            <dd>{record.summary.next_step}</dd>
            {record.summary.errors.length > 0 && (
              <>
                <dt>errors</dt>
                <dd>{record.summary.errors.join(" · ")}</dd>
              </>
            )}
            {record.summary.decisions.length > 0 && (
              <>
                <dt>decisions</dt>
                <dd>{record.summary.decisions.join(" · ")}</dd>
              </>
            )}
            {record.summary.pending.length > 0 && (
              <>
                <dt>pending</dt>
                <dd>{record.summary.pending.join(" · ")}</dd>
              </>
            )}
            {record.summary.files.length > 0 && (
              <>
                <dt>files</dt>
                <dd>
                  {record.summary.files.map((file) => (
                    <div key={file.path}>
                      <code>{file.path}</code> — {file.why}
                    </div>
                  ))}
                </dd>
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
