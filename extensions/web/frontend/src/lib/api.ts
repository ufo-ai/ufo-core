import type { CredentialRequest } from "@/lib/types";

export const BASE = "/surface/web";

export type Fetched<T> = { ok: true; payload: T } | { ok: false; message: string; status: number };

export const SESSION_FAULT_HEADER = "x-ufo-session-fault";

export type SessionFault = "expired" | "no-member";

/** Which of the two 401s the surface answered: a bearer it could not read, or a live bearer whose
 *  email holds no member row in this workspace. */
export function sessionFault(res: Response): SessionFault {
  return res.headers.get(SESSION_FAULT_HEADER) === "no-member" ? "no-member" : "expired";
}

export const REFUSAL_HEADER = "x-ufo-refusal";

const REFUSAL_MAX_CHARS = 240;

/** A read that fails states the route's own sentence where the route wrote one for the member and
 *  said so with `REFUSAL_HEADER`. A status code alone tells the member nothing about what to do,
 *  and an unmarked body is the surface talking to itself. */
async function refusal(res: Response): Promise<string> {
  const fallback = "Error " + res.status + " — reload to retry.";
  if (!res.headers.get(REFUSAL_HEADER)) return fallback;
  const body = (await res.text().catch(() => "")).trim();
  return body && body.length <= REFUSAL_MAX_CHARS ? body : fallback;
}

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<Fetched<T>> {
  try {
    const res = await fetch(BASE + path, { credentials: "same-origin", signal });
    if (!res.ok) return { ok: false, message: await refusal(res), status: res.status };
    return { ok: true, payload: (await res.json()) as T };
  } catch {
    return { ok: false, message: "Network error — try again.", status: 0 };
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
