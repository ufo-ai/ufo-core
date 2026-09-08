import type { ActionCall, ActionInput, ActionView, CredentialRequest } from "@/lib/types";

export const BASE = "/surface/web";

export type UploadRef = { key: string; sig: string };

export async function uploadAttachment(file: File): Promise<UploadRef | null> {
  try {
    const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
    const sha256 = btoa(String.fromCharCode(...new Uint8Array(digest)));
    const res = await fetch(BASE + "/uploads", {
      method: "POST",
      credentials: "same-origin",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ name: file.name, size_bytes: file.size, sha256 }),
    });
    if (!res.ok) return null;
    const plan = (await res.json()) as { key: string; put_url: string; sig: string };
    const put = await fetch(plan.put_url, {
      method: "PUT",
      body: file,
      headers: { "x-amz-checksum-sha256": sha256 },
    });
    return put.ok ? { key: plan.key, sig: plan.sig } : null;
  } catch {
    return null;
  }
}

export type Fetched<T> = { ok: true; payload: T } | { ok: false; message: string; status: number };

export const SESSION_FAULT_HEADER = "x-ufo-session-fault";

export const SIGN_IN_PATH = "/login";

export const SIGN_OUT_PATH = "/logout";

export type SessionFault = "expired" | "no-member" | "no-seat";

export function sessionFault(res: Response): SessionFault {
  const stated = res.headers.get(SESSION_FAULT_HEADER);
  return stated === "no-member" || stated === "no-seat" ? stated : "expired";
}

export const REFUSAL_HEADER = "x-ufo-refusal";

const REFUSAL_MAX_CHARS = 240;

async function refusal(res: Response): Promise<string> {
  const fallback = "Error " + res.status + " — reload to retry.";
  if (!res.headers.get(REFUSAL_HEADER)) return fallback;
  const body = (await res.text().catch(() => "")).trim();
  return body && body.length <= REFUSAL_MAX_CHARS ? body : fallback;
}

export function aborted(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    (error as { name?: unknown }).name === "AbortError"
  );
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
  url?: string | null;
};

export function actionPath(agentId: string, call: ActionCall): string {
  const target = call.name === undefined ? [call.kind] : [call.kind, call.name];
  return (
    "/agents/" + agentId + "/actions/" + [...target, call.action].map(encodeURIComponent).join("/")
  );
}

export function postAction(
  agentId: string,
  call: ActionCall,
  input: ActionInput,
): Promise<IntentOutcome> {
  return postLane(actionPath(agentId, call), input);
}

export type ObjectAction = { kind: string; name: string; action: string };

export async function postObjectAction(
  agentId: string,
  target: ObjectAction,
  input: ActionInput,
): Promise<IntentOutcome> {
  const projected = await getJson<{ actions: ActionView[] }>(
    "/actions/" + encodeURIComponent(target.kind) + "/" + encodeURIComponent(target.name),
  );
  if (!projected.ok) return { applied: false, message: projected.message };
  const view = projected.payload.actions.find((entry) => entry.name === target.action);
  if (!view) return { applied: false, message: "This act is not available here." };
  return postAction(agentId, view.call, input);
}

export function postIntent(agentId: string, envelope: unknown): Promise<IntentOutcome> {
  return postLane("/agents/" + agentId + "/intents", envelope);
}

async function postLane(path: string, body: unknown): Promise<IntentOutcome> {
  let res: Response;
  try {
    res = await fetch(BASE + path, {
      method: "POST",
      credentials: "same-origin",
      body: JSON.stringify(body),
    });
  } catch {
    return { applied: false, message: "Network error — try again." };
  }
  // A session that ended is not a refusal to read: signing in again is the only act left, so a 401 goes
  // to the one door rather than stating that the door is shut.
  if (res.status === 401) {
    window.location.assign(SIGN_IN_PATH);
    return { applied: false, message: "" };
  }
  const answered = await res.text().catch(() => "");
  let outcome: Partial<IntentOutcome> | null = null;
  try {
    outcome = JSON.parse(answered) as Partial<IntentOutcome>;
  } catch {
    // A fence refuses in its own words and in plain text. It is read on two terms: marked as written for
    // the member, and short enough to be a sentence — an upstream error page is neither.
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
