import { useSyncExternalStore } from "react";

import {
  AGENTS_HASH,
  BUILDER_HASH,
  STORE_HASH,
  agentHash,
  agentsHash,
  artifactTarget,
  bootRoute,
  chatHash,
  chatsHash,
  firstRunHash,
  homeHash,
  newChatHash,
  parseHash,
  sectionHash,
  automationsHash,
  workspaceHash,
  type PlaceStep,
  type Route,
  type Section,
  type WorkspacePlace,
  type WorkspaceTab,
} from "@/lib/route";
import { heldTrack, holdTrack, type TrackScreen } from "@/lib/tracks";

/** Five writers moved the page before this, and four worked only because the `hashchange` listener
 *  re-parsed after them — landing the route a task late, with no drawer close and no arrival. */

type Standing = {
  screen: TrackScreen;
  opens: string[] | undefined;
  land: (held: string[]) => { route: Route; hash: string };
};

function standingOn(route: Route | null): Standing | null {
  if (route === null) return null;
  if (route.kind === "home") {
    return {
      screen: "home",
      opens: route.place.opens,
      land: (held) => {
        const place = { ...route.place, opens: held };
        return { route: { kind: "home", place }, hash: homeHash(place) };
      },
    };
  }
  if (route.kind === "agent") {
    return {
      screen: `agent:${route.agentId}`,
      opens: route.place.opens,
      land: (held) => {
        const place = { ...route.place, opens: held };
        return {
          route: { kind: "agent", agentId: route.agentId, place },
          hash: agentHash(route.agentId, place),
        };
      },
    };
  }
  if (route.kind === "workspace") {
    return {
      screen: `workspace:${route.view}`,
      opens: route.place.opens,
      land: (held) => {
        const place = { ...route.place, opens: held };
        return {
          route: { kind: "workspace", view: route.view, place },
          hash: workspaceHash(route.view, place),
        };
      },
    };
  }
  if (route.kind === "section") {
    return {
      screen: `section:${route.section}`,
      opens: route.place.opens,
      land: (held) => {
        const place = { ...route.place, opens: held };
        return {
          route: { kind: "section", section: route.section, place },
          hash: sectionHash(route.section, place),
        };
      },
    };
  }
  return null;
}

export type Fleeting = (screen: TrackScreen, lane: string) => boolean;

const NOTHING_FLEETING: Fleeting = () => false;

/** A shell names the lanes it stands a member in rather than ones they carried: such a lane rides an
 *  address the member pressed for, and is dropped from one they arrived at from elsewhere. */
let fleeting = NOTHING_FLEETING;

/** An address stating a track wins and the store is written to match; stating none, the held track
 *  lands and the address is written in the same tick. It never runs while a screen renders. */
function arrive(next: Route, before: Route | null, pressed = false): Route {
  const standing = standingOn(next);
  if (!standing) return next;
  const same = standing.screen === standingOn(before)?.screen;
  if (standing.opens !== undefined) {
    const opens = standing.opens;
    const settled =
      same || pressed ? opens : opens.filter((lane) => !fleeting(standing.screen, lane));
    holdTrack(
      standing.screen,
      settled.filter((lane) => !fleeting(standing.screen, lane)),
    );
    if (settled.length === opens.length) return next;
    const landed = standing.land(settled);
    history.replaceState(null, "", landed.hash);
    return landed.route;
  }
  if (same) {
    holdTrack(standing.screen, []);
    return next;
  }
  const kept = heldTrack(standing.screen);
  if (!kept.length) return next;
  const landed = standing.land(kept);
  history.replaceState(null, "", landed.hash);
  return landed.route;
}

let held: Route | null = null;
let travelled = 0;
const listeners = new Set<() => void>();

export function heldRoute(): Route {
  held ??= bootRoute(location.hash, location.search);
  return held;
}

function publish(route: Route, step: PlaceStep = "push"): void {
  held = route;
  if (step !== "replace") travelled += 1;
  for (const listener of listeners) listener();
}

export function useRoute(): Route {
  return useSyncExternalStore((listener) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }, heldRoute);
}

const travelCount = () => travelled;

/** How many times the member has moved: a push, a back, or an address they typed. A `replace`
 *  writes where the page already stands, so it is not a move — a surface that answers travel reads
 *  this rather than the route, whose identity every replacement changes. */
export function useTravel(): number {
  return useSyncExternalStore((listener) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }, travelCount);
}

/** `back` unwinds the entry a push left rather than writing the address it was handed: the caller
 *  states where the page would stand, and the browser lands on the entry that already says it. */
export function navigate(hash: string, step: PlaceStep = "push"): void {
  if (step === "back") {
    const target = parseHash(hash);
    if (standingOn(target)?.opens !== undefined) arrive(target, heldRoute(), true);
    history.back();
    return;
  }
  if (step === "replace") history.replaceState(null, "", hash);
  else if (location.hash !== hash) location.hash = hash;
  publish(arrive(parseHash(hash), heldRoute(), true), step);
}

const readAddress = () => publish(arrive(parseHash(location.hash), heldRoute()));

const CONNECTED_PARAM = "connected";

/** The grant landed in a document this one replaced, so the address is all the screen it lands on has
 *  to say what happened. `startRouter` takes it off the address as the page opens, which spends it. */
export function connectArrival(): string {
  return new URLSearchParams(location.search).get(CONNECTED_PARAM) ?? "";
}

export function startRouter(options: { fleeting?: Fleeting } = {}): () => void {
  fleeting = options.fleeting ?? NOTHING_FLEETING;
  const target = artifactTarget(location.search);
  if (target) location.replace(target);
  const booted = heldRoute();
  if (connectArrival()) {
    const params = new URLSearchParams(location.search);
    params.delete(CONNECTED_PARAM);
    const query = params.toString();
    history.replaceState(null, "", location.pathname + (query ? "?" + query : "") + location.hash);
  }
  if (!location.hash) {
    if (booted.kind === "chat") history.replaceState(null, "", chatHash(booted.conversationId));
    if (booted.kind === "first-run") history.replaceState(null, "", firstRunHash(booted.step));
  }
  publish(arrive(booted, null));
  window.addEventListener("hashchange", readAddress);
  return () => {
    window.removeEventListener("hashchange", readAddress);
    held = null;
  };
}

export function openHome(): void {
  navigate(homeHash());
}

export function placeHome(place: WorkspacePlace, step: PlaceStep = "push"): void {
  stepPlace(step, heldRoute().kind === "home", () => homeHash(place));
}

export function openChat(conversationId: string): void {
  navigate(chatHash(conversationId));
}

export function openSlot(conversationId: string, slot: string | null): void {
  navigate(chatHash(conversationId, slot ?? undefined));
}

export function openNewChat(agentId: string): void {
  navigate(newChatHash(agentId));
}

/** The wizard mounts only behind a member's press or a run already in flight: its address alone must
 *  not found a conversation, or Back and reload would send model turns nobody asked for. */
let building = false;

export function buildWanted(): boolean {
  return building;
}

export function openAgents(): void {
  building = false;
  navigate(AGENTS_HASH);
}

export function forwardAgents(): void {
  building = false;
  navigate(AGENTS_HASH, "replace");
}

export function openBuilder(): void {
  building = true;
  navigate(BUILDER_HASH);
}

export function openStore(): void {
  navigate(STORE_HASH);
}

export function openChats(): void {
  navigate(chatsHash());
}

export function placeChats(place: WorkspacePlace, step: PlaceStep): void {
  stepPlace(step, heldRoute().kind === "chats", () => chatsHash(place));
}

export function openAutomations(): void {
  navigate(automationsHash());
}

export function openAgent(agentId: string): void {
  navigate(agentHash(agentId));
}

export function openAgentPlace(agentId: string, place: WorkspacePlace): void {
  navigate(agentHash(agentId, place));
}

/** Pushed from anywhere, but replaced or unwound only by the screen the member is standing on: a pane
 *  reporting a place it was already leaving must not rewrite where they went. */
function stepPlace(step: PlaceStep, standing: boolean, hash: () => string | null): void {
  if (step !== "push" && !standing) return;
  const to = hash();
  if (to === null) return;
  navigate(to, step);
}

export function placeAgent(place: WorkspacePlace, step: PlaceStep): void {
  const seen = heldRoute();
  stepPlace(step, seen.kind === "agent", () =>
    seen.kind === "agent" ? agentHash(seen.agentId, place) : null,
  );
}

export function placeAgents(place: WorkspacePlace, step: PlaceStep): void {
  stepPlace(step, heldRoute().kind === "agents", () => agentsHash(place));
}

export function placeWorkspace(view: WorkspaceTab, place: WorkspacePlace, step: PlaceStep): void {
  const seen = heldRoute();
  stepPlace(step, seen.kind === "workspace" && seen.view === view, () =>
    workspaceHash(view, place),
  );
}

export function placeSection(section: Section, place: WorkspacePlace, step: PlaceStep): void {
  const seen = heldRoute();
  stepPlace(step, seen.kind === "section" && seen.section === section, () =>
    sectionHash(section, place),
  );
}

export function placeAutomations(place: WorkspacePlace, step: PlaceStep): void {
  stepPlace(step, heldRoute().kind === "automations", () => automationsHash(place));
}

export function placeFirstRun(step: string | undefined): void {
  stepPlace("replace", heldRoute().kind === "first-run", () => firstRunHash(step));
}

export function resetRouter(): void {
  held = null;
  travelled = 0;
  building = false;
  fleeting = NOTHING_FLEETING;
}
