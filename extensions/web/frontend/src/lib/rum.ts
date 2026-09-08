import type { DatadogRum, RumInitConfiguration } from "@datadog/browser-rum";

/** The SDK is imported where it starts rather than at the top: it is 184 kB, the page loads one script,
 *  and that script is what the member waits for. The recorder snapshots the DOM it finds. */

const CONFIG_ID = "rum";
const SERVICE = "ufo-portal";
const FIELDS = ["applicationId", "clientToken", "site", "env", "version"] as const;

export type RumDeploy = Record<(typeof FIELDS)[number], string>;

let recording: Promise<DatadogRum> | null = null;

/** The surface writes the block and refuses to serve a page that declares none, so an absent block is a
 *  page no deploy serves. A block written half-way is said rather than recorded against. */
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

export function identifyRum(email: string): void {
  void recording?.then((rum) => rum.setUser({ id: email, email }));
}
