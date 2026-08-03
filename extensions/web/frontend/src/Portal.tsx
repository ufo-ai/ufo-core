import { useEffect, useState } from "react";

import { App } from "@/App";
import { TokenCard } from "@/views/TokenCard";
import { BASE } from "@/lib/api";
import type { AgentsPayload } from "@/lib/types";

type Boot =
  | { phase: "loading" }
  | { phase: "signed-out" }
  | { phase: "failed"; message: string }
  | { phase: "ready"; payload: AgentsPayload };

export function Portal() {
  const [boot, setBoot] = useState<Boot>({ phase: "loading" });

  useEffect(() => {
    let live = true;
    (async () => {
      let res: Response | null;
      try {
        res = await fetch(BASE + "/api/agents", { credentials: "same-origin" });
      } catch {
        res = null;
      }
      if (!live) return;
      if (!res || res.status === 401) {
        setBoot({ phase: "signed-out" });
        return;
      }
      if (!res.ok) {
        setBoot({ phase: "failed", message: "Error " + res.status + " — reload to retry." });
        return;
      }
      let payload: AgentsPayload;
      try {
        payload = (await res.json()) as AgentsPayload;
      } catch {
        setBoot({ phase: "failed", message: "Network error — try again." });
        return;
      }
      if (!live) return;
      setBoot({ phase: "ready", payload });
    })();
    return () => {
      live = false;
    };
  }, []);

  if (boot.phase === "loading")
    return <div className="m-auto max-w-empty text-center opacity-(--muted-soft)">Loading…</div>;
  if (boot.phase === "signed-out") return <TokenCard />;
  if (boot.phase === "failed") {
    return (
      <div className="m-auto max-w-empty text-center opacity-(--muted-soft)">
        {boot.message}
      </div>
    );
  }
  return <App agents={boot.payload.agents} member={boot.payload.member} />;
}
