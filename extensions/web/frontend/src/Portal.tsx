import { useCallback, useEffect, useState } from "react";

import logo from "@/assets/ufo-logo.svg";
import { App } from "@/App";
import { FAULTS, SignIn } from "@/views/SignIn";
import { BASE, SIGN_IN_PATH, sessionFault, type SessionFault } from "@/lib/api";
import { parseHash } from "@/lib/route";
import { titled } from "@/lib/title";
import type { AgentsPayload } from "@/lib/types";

type Boot =
  | { phase: "loading" }
  | { phase: "signed-out"; fault: SessionFault }
  | { phase: "failed"; message: string }
  | { phase: "ready"; payload: AgentsPayload };

async function readAgents(): Promise<Boot> {
  let res: Response | null;
  try {
    res = await fetch(BASE + "/api/agents", { credentials: "same-origin" });
  } catch {
    res = null;
  }
  if (!res) return { phase: "failed", message: "Network error — try again." };
  if (res.status === 401) return { phase: "signed-out", fault: sessionFault(res) };
  if (!res.ok) return { phase: "failed", message: "Error " + res.status + " — reload to retry." };
  try {
    return { phase: "ready", payload: (await res.json()) as AgentsPayload };
  } catch {
    return { phase: "failed", message: "Network error — try again." };
  }
}

export function Portal() {
  const [boot, setBoot] = useState<Boot>({ phase: "loading" });

  useEffect(() => {
    let live = true;
    readAgents().then((next) => {
      if (live) setBoot(next);
    });
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    if (boot.phase !== "signed-out") return;
    if (boot.fault === "expired") {
      const route = parseHash(window.location.hash);
      const carried = "conversationId" in route ? "?c=" + route.conversationId : "";
      window.location.assign(SIGN_IN_PATH + carried);
      return;
    }
    document.title = titled(FAULTS[boot.fault].title);
  }, [boot]);

  /** A re-read after an agent is created lands the new row everywhere the audience is read — the
   *  cards, the sidebar's picker, and the router that opens one. It replaces a ready answer only:
   *  the member is standing in a working portal, and a re-read that failed is not a reason to take
   *  it away from them. */
  const reload = useCallback(async () => {
    const next = await readAgents();
    if (next.phase === "ready") setBoot(next);
  }, []);

  if (boot.phase === "loading")
    return <div className="m-auto max-w-empty text-center text-ink-soft">Loading…</div>;
  if (boot.phase === "signed-out" && boot.fault === "no-member")
    return (
      <div className="flex min-h-dvh flex-col items-center justify-center gap-4xl p-2xl">
        <span
          role="img"
          aria-label="ufo"
          className="h-(--size-wordmark) w-(--size-logo) shrink-0 bg-current"
          style={{ mask: `url(${logo}) center / contain no-repeat` }}
        />
        <SignIn fault={boot.fault} />
      </div>
    );
  if (boot.phase === "signed-out")
    return <div className="m-auto max-w-empty text-center text-ink-soft">Loading…</div>;
  if (boot.phase === "failed") {
    return (
      <div className="m-auto max-w-empty text-center text-ink-soft">
        {boot.message}
      </div>
    );
  }
  return (
    <App
      agents={boot.payload.agents}
      member={boot.payload.member}
      onAgents={reload}
    />
  );
}
