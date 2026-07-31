import type { CredentialRequest } from "@/lib/types";

export const BASE = "/surface/web";

export type Fetched<T> = { ok: true; payload: T } | { ok: false; message: string };

export async function getJson<T>(path: string): Promise<Fetched<T>> {
  try {
    const res = await fetch(BASE + path, { credentials: "same-origin" });
    if (!res.ok) return { ok: false, message: "Error " + res.status + " — reload to retry." };
    return { ok: true, payload: (await res.json()) as T };
  } catch {
    return { ok: false, message: "Network error — try again." };
  }
}

export type IntentOutcome = {
  applied: boolean;
  message: string;
  turn_id?: string;
  credentials?: CredentialRequest | null;
};

export async function postIntent(agentId: string, envelope: unknown): Promise<IntentOutcome> {
  let res: Response;
  try {
    res = await fetch(BASE + "/agents/" + agentId + "/intents", {
      method: "POST",
      credentials: "same-origin",
      body: JSON.stringify(envelope),
    });
  } catch {
    return { applied: false, message: "Network error — try again." };
  }
  const outcome = (await res.json().catch(() => null)) as Partial<IntentOutcome> | null;
  if (!outcome) return { applied: false, message: "Error " + res.status + " — try again." };
  return {
    applied: Boolean(outcome.applied),
    message: outcome.message ?? "Error " + res.status + " — try again.",
    turn_id: outcome.turn_id,
    credentials: outcome.credentials,
  };
}
