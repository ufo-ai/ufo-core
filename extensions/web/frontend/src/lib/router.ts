import { useSyncExternalStore } from "react";

import {
  BUILDER_HASH,
  HOME_CONNECTORS_LANE,
  HOME_NEW_LANE,
  agentHash,
  artifactTarget,
  bootRoute,
  chatHash,
  firstRunHash,
  homeHash,
  newChatHash,
  parseHash,
  sectionHash,
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

/** An address stating a track wins and the store is written to match; stating none, the store hands
 *  back the track the screen was left holding and the address is written in the same tick. */
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
  const kept = heldTrack(standing.screen);
  if (!kept.length) return next;
  const landed = standing.land(kept);
  history.replaceState(null, "", landed.hash);
  return landed.route;
}

function fleeting(screen: TrackScreen, lane: string): boolean {
  return screen === "home" && lane === HOME_NEW_LANE;
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
    arrive(parseHash(hash), heldRoute(), true);
    history.back();
    return;
  }
  if (step === "replace") history.replaceState(null, "", hash);
  else if (location.hash !== hash) location.hash = hash;
  publish(arrive(parseHash(hash), heldRoute(), true), step);
}

const readAddress = () => publish(arrive(parseHash(location.hash), heldRoute()));

const CONNECTED_PARAM = "connected";

export function connectArrival(): string {
  return new URLSearchParams(location.search).get(CONNECTED_PARAM) ?? "";
}

export function startRouter(): () => void {
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

export function openHomeWithConnectors(lane: string): void {
  navigate(homeHash({ opens: [lane, HOME_CONNECTORS_LANE] }));
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

export function openApps(): void {
  navigate(workspaceHash("apps"));
}

export function forwardApps(): void {
  navigate(workspaceHash("apps"), "replace");
}

export function openBuilder(): void {
  navigate(BUILDER_HASH);
}

export function openAgent(agentId: string): void {
  navigate(agentHash(agentId));
}

export function openAgentPlace(agentId: string, place: WorkspacePlace): void {
  navigate(agentHash(agentId, place));
}

function stepPlace(step: PlaceStep, standing: boolean, hash: () => string | null): void {
  if (step !== "push" && !standing) return;
  const to = hash();
  if (to === null) return;
  navigate(to, step);
}

export function placeHome(place: WorkspacePlace, step: PlaceStep = "push"): void {
  stepPlace(step, heldRoute().kind === "home", () => homeHash(place));
}

export function placeAgent(place: WorkspacePlace, step: PlaceStep): void {
  const seen = heldRoute();
  stepPlace(step, seen.kind === "agent", () =>
    seen.kind === "agent" ? agentHash(seen.agentId, place) : null,
  );
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

export function placeFirstRun(step: string | undefined): void {
  stepPlace("replace", heldRoute().kind === "first-run", () => firstRunHash(step));
}

export function resetRouter(): void {
  held = null;
  travelled = 0;
}
