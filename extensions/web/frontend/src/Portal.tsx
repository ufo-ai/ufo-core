import { useCallback, useEffect, useState } from "react";

import { App } from "@/App";
import { Empty, Loading } from "@/kernel/panel";
import { Frame } from "@/views/Frame";
import { FAULTS, SignIn } from "@/views/SignIn";
import { BASE, SIGN_IN_PATH, sessionFault, type SessionFault } from "@/lib/api";
import { parseHash } from "@/lib/route";
import { identifyRum } from "@/lib/rum";
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
  // A seat this workspace took away refuses with 403 and names itself in the fault header; every other
  // 403 is a route refusing one read, which a reload can still answer.
  if (res.status === 403 && sessionFault(res) === "no-seat")
    return { phase: "signed-out", fault: "no-seat" };
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
    if (boot.phase === "ready") identifyRum(boot.payload.member.email);
  }, [boot]);

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

  const reload = useCallback(async () => {
    const next = await readAgents();
    if (next.phase === "ready") setBoot(next);
  }, []);

  if (boot.phase === "loading")
    return (
      <Empty>
        <Loading />
      </Empty>
    );
  if (boot.phase === "signed-out" && boot.fault !== "expired")
    return (
      <Frame>
        <SignIn fault={boot.fault} />
      </Frame>
    );
  if (boot.phase === "signed-out")
    return (
      <Empty>
        <Loading />
      </Empty>
    );
  if (boot.phase === "failed") return <Empty>{boot.message}</Empty>;
  return (
    <App
      agents={boot.payload.agents}
      archived={boot.payload.archived}
      member={boot.payload.member}
      surfaces={boot.payload.surfaces}
      onAgents={reload}
    />
  );
}
