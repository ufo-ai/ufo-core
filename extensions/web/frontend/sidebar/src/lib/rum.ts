import type { DatadogRum, RumInitConfiguration } from "@datadog/browser-rum";

/** Datadog Real User Monitoring, including the session recording.
 *
 *  The recording is made in the browser — a stream of DOM changes the SDK captures — so nothing
 *  server-side can produce one and the SDK has to ship in the bundle. What the deploy decides is
 *  whether it runs at all: one image serves every deploy, so the application, the token, and the
 *  environment arrive in the page the surface serves rather than in the build. A deploy that
 *  states none records nothing, and the SDK is never fetched there.
 *
 *  The SDK is imported where it starts rather than at the top of this file: it is 184 kB, the page
 *  loads one script, and that script is what the member waits for. Fetched beside the page instead,
 *  it costs the first few hundred milliseconds of a session — the recorder snapshots the DOM it
 *  finds, so the replay opens on the page as rendered, and a fault thrown before the fetch lands
 *  is the one thing this misses. */

const CONFIG_ID = "rum";
const SERVICE = "ufo-portal";
const FIELDS = ["applicationId", "clientToken", "site", "env", "version"] as const;

export type RumDeploy = Record<(typeof FIELDS)[number], string>;

let recording: Promise<DatadogRum> | null = null;

/** What this deploy states, or null where it states nothing. The surface writes the block and
 *  refuses to serve a page that declares none, so an absent block is a page no deploy serves and
 *  it records nothing. A block written half-way is the contract broken, and is said rather than
 *  recorded against: sessions reaching the wrong application are found by nobody. */
export function heldRum(): RumDeploy | null {
  const block = document.getElementById(CONFIG_ID);
  if (!block) return null;
  const held: unknown = JSON.parse(block.textContent || "null");
  if (held === null) return null;
  const named = held as Record<string, unknown>;
  for (const field of FIELDS) {
    if (typeof named[field] !== "string" || !named[field]) {
      throw new Error("the rum block states no " + field);
    }
  }
  return named as RumDeploy;
}

/** How the portal is recorded. `mask-user-input` is the load-bearing line: every field a member
 *  types into is masked in the replay — a password input is masked whatever this says — while the
 *  rendered page stays readable, so a replay shows the screen the member was looking at. */
export function rumOptions(deploy: RumDeploy): RumInitConfiguration {
  return {
    applicationId: deploy.applicationId,
    clientToken: deploy.clientToken,
    site: deploy.site,
    service: SERVICE,
    env: deploy.env,
    version: deploy.version,
    sessionSampleRate: 100,
    sessionReplaySampleRate: 100,
    defaultPrivacyLevel: "mask-user-input",
    trackUserInteractions: true,
    trackResources: true,
    trackLongTasks: true,
  };
}

export function startRum(deploy: RumDeploy): void {
  recording = import("@datadog/browser-rum").then(({ datadogRum }) => {
    datadogRum.init(rumOptions(deploy));
    return datadogRum;
  });
}

/** Name the session after the member the boot read authenticated, so a replay is reachable from
 *  the member who reported the fault. The email is the web surface's own identity for them. */
export function identifyRum(email: string): void {
  void recording?.then((rum) => rum.setUser({ id: email, email }));
}
