import { homeHash } from "@/lib/route";
import { navigate } from "@/lib/router";
import { heldTrack, type TrackScreen } from "@/lib/tracks";

export const HOME_NEW_LANE = "new";

export const HOME_CONNECTORS_LANE = "connectors";

const HOME_LANE_INSTANCE = ".";

export function mintHomeLane(agentId: string, taken: readonly string[]): string {
  if (!taken.includes(agentId)) return agentId;
  let instance = 2;
  while (taken.includes(agentId + HOME_LANE_INSTANCE + instance)) instance += 1;
  return agentId + HOME_LANE_INSTANCE + instance;
}

const HOME_LANE_CONVERSATION = "c:";

export function homeConversationLane(conversationId: string): string {
  return HOME_LANE_CONVERSATION + conversationId;
}

export function homeLaneConversation(lane: string): string | null {
  return lane.startsWith(HOME_LANE_CONVERSATION)
    ? lane.slice(HOME_LANE_CONVERSATION.length)
    : null;
}

export function homeLaneAgent(lane: string): string | null {
  if (lane === HOME_NEW_LANE || lane === HOME_CONNECTORS_LANE) return null;
  if (lane.startsWith(HOME_LANE_CONVERSATION)) return null;
  const [agentId] = lane.split(HOME_LANE_INSTANCE);
  return agentId;
}

/** The composer lane is where a member arrives, not something they carried: it stands only while they
 *  are on home, so an address that names it from elsewhere lands without it. */
export function fleetingHomeLane(screen: TrackScreen, lane: string): boolean {
  return screen === "home" && lane === HOME_NEW_LANE;
}

/** The rail's home mark restates the lanes the member arranged: a bare home address would stand the
 *  screen afresh. */
export function openHomeLanes(): void {
  navigate(homeHash({ opens: heldTrack("home") }));
}

export function openHomeWithConnectors(lane: string): void {
  navigate(homeHash({ opens: [lane, HOME_CONNECTORS_LANE] }));
}
