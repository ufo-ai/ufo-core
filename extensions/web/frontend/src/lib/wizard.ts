export const APP_CREATOR_TITLE = "App Creator";

/** Not the chat screen's `new:<agentId>`, so neither pane's founding send can ever hold the other's
 *  busy, and a wizard mount finds nothing on the key it watches but its own run. */
export function wizardKey(agentId: string): string {
  return "wizard:" + agentId;
}
