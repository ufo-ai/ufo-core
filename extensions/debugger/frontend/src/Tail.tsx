import { useEffect, useRef, useState } from "react";
import { apiUrl } from "./api";

type TailEvent = { kind: string; data: string };
const EVENT_KINDS = [
  "text",
  "tool",
  "skill",
  "subagent_activity",
  "cost",
  "absorbed",
  "resumed",
  "reply",
  "parked",
  "terminal",
] as const;

export function Tail(props: { turnId: string }) {
  const [events, setEvents] = useState<TailEvent[]>([]);
  const [live, setLive] = useState(true);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const source = new EventSource(apiUrl(`turns/${props.turnId}/stream`));
    const push = (kind: string) => (event: MessageEvent) => {
      setEvents((current) => [...current, { kind, data: event.data as string }]);
      if (kind === "terminal" || kind === "parked") {
        source.close();
        setLive(false);
      }
    };
    for (const kind of EVENT_KINDS) source.addEventListener(kind, push(kind));
    source.onerror = () => {
      source.close();
      setLive(false);
    };
    return () => source.close();
  }, [props.turnId]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [events]);

  return (
    <details open>
      <summary>live tail {live ? "· streaming" : "· ended"}</summary>
      <div className="tail-log" ref={logRef}>
        {events.map((event, index) =>
          event.kind === "text" ? (
            <span key={index}>{text(event.data)}</span>
          ) : (
            <pre key={index}>
              {event.kind}: {event.data}
            </pre>
          ),
        )}
      </div>
    </details>
  );
}

function text(data: string): string {
  try {
    return (JSON.parse(data) as { text?: string }).text ?? "";
  } catch {
    return "";
  }
}
