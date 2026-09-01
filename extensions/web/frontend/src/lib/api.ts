import type { ActionCall, ActionInput, ActionView, CredentialRequest } from "@/lib/types";

export const BASE = "/surface/web";

/** Carry one file's bytes to the blob store and answer the key they landed under, or null when
 *  this deploy signs no upload URL and the send must carry the file itself. The surface measures
 *  the URL by the size and the sha256 named here, so the PUT must be exactly this file and must
 *  carry the checksum header the signature covers. */
export async function uploadAttachment(file: File): Promise<string | null> {
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
    const plan = (await res.json()) as { key: string; put_url: string };
    const put = await fetch(plan.put_url, {
      method: "PUT",
      body: file,
      headers: { "x-amz-checksum-sha256": sha256 },
    });
    return put.ok ? plan.key : null;
  } catch {
    return null;
  }
}

export type Fetched<T> = { ok: true; payload: T } | { ok: false; message: string; status: number };

export const SESSION_FAULT_HEADER = "x-ufo-session-fault";

/** The one sign-in door. A session that has ended sends the member here rather than stating that
 *  it ended: signing in again is the only act left, and the page they land on asks for it. */
export const SIGN_IN_PATH = "/login";

/** The way back to the form. The door above forwards a browser that already holds a session, so an
 *  address other than the one the cookie proves is reached by clearing it first. */
export const SIGN_OUT_PATH = "/logout";

export type SessionFault = "expired" | "no-member" | "no-seat";

/** Which refusal the surface answered: a bearer it could not read, a live bearer whose email holds
 *  no member row in this workspace, or a live member whose seat this workspace took away. */
export function sessionFault(res: Response): SessionFault {
  const stated = res.headers.get(SESSION_FAULT_HEADER);
  return stated === "no-member" || stated === "no-seat" ? stated : "expired";
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

/** A GET of the portal API, answered as `{ok, payload}` or `{ok: false, message, status}`; the
 * message is the route's own sentence to the member. */
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

/** The route a presented action posts on: the agent's lane, then the target the view's call
 *  template names — its kind, the row for an instance action — and the action. The body is the
 *  action's own input and nothing else; the route binds the target, so a page never states where
 *  an act lands, only what it says. */
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

/** The object one act installs against, and the action on it: what a screen knows about the act it
 *  offers before any read. */
export type ObjectAction = { kind: string; name: string; action: string };

/** One act on one object, read and posted in a single press: the row's acts are projected from
 *  their declarations, and the named action posts with the call template that projection bound. An
 *  action the deploy does not present answers as a refusal in the read's own words — the press never
 *  authors a call the portal did not offer, and who may run it is the action's own gate. */
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

/** Post a prepared intent to an agent — the one mutation path a page has; the turn is the chat
 * transport and the audit record. */
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
