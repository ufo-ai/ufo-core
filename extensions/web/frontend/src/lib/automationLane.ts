const AUTOMATION_PREFIX = "automation/";
const PART = "/";

export type AutomationLane = { agent: string; kind: string; name: string };

/** The lane id one automation's Details opens under. Spotlight mints a hit's address with it, so a
 *  hit lands on the Automations screen with that automation open. */
export function automationId(at: AutomationLane): string {
  return AUTOMATION_PREFIX + [at.agent, at.kind, at.name].join(PART);
}

export function automationLane(id: string): AutomationLane | null {
  if (!id.startsWith(AUTOMATION_PREFIX)) return null;
  const [agent = "", kind = "", name = ""] = id.slice(AUTOMATION_PREFIX.length).split(PART);
  return { agent, kind, name };
}
