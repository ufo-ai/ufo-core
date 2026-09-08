import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { BASE, REFUSAL_HEADER, aborted, getJson } from "@/lib/api";
import { cn } from "@/lib/cn";

export type ApplicationActionRecord = {
  label: string;
  write: {
    function: "ufoWrite";
    arguments: [kind: string, name: string, spec: Record<string, unknown>];
  };
  read: {
    function: "ufoRead";
    arguments: [path: string];
  };
};

type ActionDetail = {
  status?: { result?: unknown };
};

type ActionOutcome = {
  ok?: boolean;
  detail?: unknown;
};

/** Apply one connector-supplied prepared action and show its durable result. */
export function ApplicationAction({
  action,
  className,
}: {
  action: ApplicationActionRecord;
  className?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const readPath = "/" + action.read.arguments[0].replace(/^\/+/, "");

  useEffect(() => {
    const controller = new AbortController();
    void getJson<ActionDetail>(readPath, controller.signal)
      .then((answer) => {
        if (!answer.ok) return;
        const result = answer.payload.status?.result;
        if (typeof result === "string") setMessage(result);
      })
      .catch((error: unknown) => {
        if (!aborted(error)) setMessage("Network error — try again.");
      });
    return () => controller.abort();
  }, [readPath]);

  const apply = async () => {
    setBusy(true);
    setMessage("");
    const [kind, name, spec] = action.write.arguments;
    try {
      const response = await fetch(BASE + "/objects/" + encodeURIComponent(kind), {
        method: "POST",
        credentials: "same-origin",
        body: JSON.stringify({ name, spec }),
      });
      const body = (await response.json().catch(() => null)) as ActionOutcome | null;
      const detail = typeof body?.detail === "string" ? body.detail : "";
      if (!response.ok || body?.ok !== true || !detail) {
        setMessage(
          response.headers.get(REFUSAL_HEADER) ||
            detail ||
            (response.ok ? "The action returned no result." : `Error ${response.status} — try again.`),
        );
        return;
      }
      setMessage(detail);
    } catch {
      setMessage("Network error — try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={cn("flex flex-col items-start gap-sm", className)}>
      <Button busy={busy} onClick={() => void apply()}>
        {action.label}
      </Button>
      <p role="status" aria-live="polite" className="min-h-[1lh] text-small text-ink-soft">
        {message}
      </p>
    </div>
  );
}
