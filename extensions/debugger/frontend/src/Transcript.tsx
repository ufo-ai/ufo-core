import { useEffect, useState } from "react";
import { ContentBlock, get, Transcript as TranscriptRecord, TranscriptMessage } from "./api";

export function Transcript(props: { conversationId: string }) {
  const [transcript, setTranscript] = useState<TranscriptRecord | null | "missing">(null);

  useEffect(() => {
    setTranscript(null);
    get<TranscriptRecord>(`conversations/${props.conversationId}/transcript`)
      .then(setTranscript)
      .catch(() => setTranscript("missing"));
  }, [props.conversationId]);

  if (transcript === null) return <div className="empty">loading…</div>;
  if (transcript === "missing") return <div className="empty">no transcript persisted yet</div>;
  return (
    <section className="panel">
      <h2>
        transcript · seq {transcript.seq} · {transcript.messages.length} messages
      </h2>
      {transcript.messages.map((message, index) => (
        <Bubble key={index} message={message} />
      ))}
    </section>
  );
}

function Bubble(props: { message: TranscriptMessage }) {
  const { message } = props;
  return (
    <div className={`bubble ${message.role}`}>
      <div className="role">{message.role}</div>
      {typeof message.content === "string" ? (
        <pre>{message.content}</pre>
      ) : (
        message.content.map((block, index) => <Block key={index} block={block} />)
      )}
    </div>
  );
}

function Block(props: { block: ContentBlock }) {
  const { block } = props;
  switch (block.type) {
    case "text":
      return <pre>{block.text}</pre>;
    case "image":
      return <div className="meta">[image]</div>;
    case "tool_use":
      return (
        <details>
          <summary>
            tool_use · <code>{block.name}</code>
          </summary>
          <pre>{JSON.stringify(block.input, null, 2)}</pre>
        </details>
      );
    case "tool_result":
      return (
        <details>
          <summary className={block.is_error ? "error-text" : undefined}>
            tool_result{block.is_error ? " · error" : ""}
          </summary>
          {typeof block.content === "string" ? (
            <pre>{block.content}</pre>
          ) : (
            block.content.map((inner, index) => <Block key={index} block={inner} />)
          )}
        </details>
      );
  }
}
