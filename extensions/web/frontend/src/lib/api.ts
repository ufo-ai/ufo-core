import type { CredentialRequest } from "@/lib/types";

export const BASE = "/surface/web";

export type Fetched<T> = { ok: true; payload: T } | { ok: false; message: string; status: number };

export const SESSION_FAULT_HEADER = "x-ufo-session-fault";

/** The one sign-in door. A session that has ended sends the member here rather than stating that
 *  it ended: signing in again is the only act left, and the page they land on asks for it. */
export const SIGN_IN_PATH = "/login";

/** The way back to the form. The door above forwards a browser that already holds a session, so an
 *  address other than the one the cookie proves is reached by clearing it first. */
export const SIGN_OUT_PATH = "/logout";

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
  /** The provider link a connect intent's tool minted for the member who submitted it. */
  url?: string | null;
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
  // A session that ended is not a refusal to read: signing in again is the only act left, so the
  // member goes to the one door rather than reading that the door is shut. It is the boot read's
  // own answer to a 401, taken here because a press is where a session is usually found to be over.
  if (res.status === 401) {
    window.location.assign(SIGN_IN_PATH);
    return { applied: false, message: "" };
  }
  const answered = await res.text().catch(() => "");
  let outcome: Partial<IntentOutcome> | null = null;
  try {
    outcome = JSON.parse(answered) as Partial<IntentOutcome>;
  } catch {
    // A fence refuses in its own words and in plain text — the bridge's endpoint and verb tables
    // answer before the lane is reached at all. Reading those words is the difference between a
    // member being told why an act is not available here and being told "Error 400". It is read
    // on the same two terms `refusal` reads a failed GET on: marked as written for the member,
    // and short enough to be a sentence — an upstream error page is neither.
    const said = answered.trim();
    const marked = Boolean(res.headers.get(REFUSAL_HEADER)) && said.length <= REFUSAL_MAX_CHARS;
    return {
      applied: false,
      message: marked && said ? said : "Error " + res.status + " — try again.",
    };
  }
  if (!outcome) return { applied: false, message: "Error " + res.status + " — try again." };
  return {
    applied: Boolean(outcome.applied),
    message: outcome.message ?? "Error " + res.status + " — try again.",
    turn_id: outcome.turn_id,
    credentials: outcome.credentials,
    url: outcome.url,
  };
}
