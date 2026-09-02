import { useSyncExternalStore } from "react";

import {
  AGENTS_HASH,
  BUILDER_HASH,
  FIRST_RUN_HASH,
  HOME_HASH,
  agentHash,
  artifactTarget,
  bootRoute,
  chatHash,
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

/** The portal's router: the one writer of the address, and the store every screen reads its route
 *  from. Five writers moved the page before this — the shell's own `go`, two spotlight handlers, an
 *  agent's settings, and a framed page's `navigate` message — and the four beside `go` worked only
 *  because the `hashchange` listener re-parsed after them, so each landed the route a task late,
 *  with no drawer close and no arrival. Every act that moves the page comes through `navigate` now,
 *  and nothing else writes `location.hash`.
 *
 *  The store is read with `useSyncExternalStore`, so no screen holds the route in state of its own
 *  and nothing writes the address or the track store while React renders. */

/** A screen a track can stand on: the screen's own name in the store, the track its address states,
 *  and what that screen becomes once a track is written back onto it. Three route kinds carry a
 *  place; a route carrying none carries no track either. */
type Standing = {
  screen: TrackScreen;
  opens: string[] | undefined;
  land: (held: string[]) => { route: Route; hash: string };
};

function standingOn(route: Route | null): Standing | null {
  if (route === null) return null;
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

/** Which of the address and the store owns a screen's track, decided once for every way a member
 *  reaches a screen — boot, a hash the browser moved, and every act that moves the page.
 *
 *  An address stating a track states it on purpose: a link a member was sent, a bookmark, a step
 *  back onto one. It wins, and the store is written to match. An address stating none is the member
 *  arriving from somewhere else — a sidebar row, a pinned row, a spotlight hit, all of which name a
 *  screen and nothing on it — so the store hands back the track the screen was left holding and the
 *  address is written to match in the same tick, leaving the two no frame to stand apart in. Coming
 *  back to a screen is not a new place, so that address is replaced: pushed, Back would walk the
 *  member through their own arrivals instead of out of the screen.
 *
 *  Standing on the screen already, the address owns whatever it says, an empty track included —
 *  that is the member closing the last slot, and a store answering over it would reopen what they
 *  had just shut.
 *
 *  It writes the track store and it writes the address, so it runs when the router starts and when
 *  it navigates, never while a screen renders. */
function arrive(next: Route, before: Route | null): Route {
  const standing = standingOn(next);
  if (!standing) return next;
  if (standing.opens || standing.screen === standingOn(before)?.screen) {
    holdTrack(standing.screen, standing.opens ?? []);
    return next;
  }
  const kept = heldTrack(standing.screen);
  if (!kept.length) return next;
  const landed = standing.land(kept);
  history.replaceState(null, "", landed.hash);
  return landed.route;
}

let held: Route | null = null;
const listeners = new Set<() => void>();

/** The route the portal stands on. Before the router starts, the address itself answers — the same
 *  read the router opens with, so a first render draws the screen the address names without anything
 *  being written to hold it there. */
export function heldRoute(): Route {
  held ??= bootRoute(location.hash, location.search);
  return held;
}

function publish(route: Route): void {
  held = route;
  for (const listener of listeners) listener();
}

export function useRoute(): Route {
  return useSyncExternalStore((listener) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }, heldRoute);
}

/** Every act that moves the page comes through here: the address is written once, read back through
 *  the table, and published as the route every screen renders from. The hash is the whole statement
 *  of where the member is, so a route this publishes cannot disagree with the address that carries
 *  it.
 *
 *  `push` leaves a history entry and `replace` writes over the one standing. `back` unwinds the entry
 *  a push left rather than writing the address it was handed: the caller states where the page would
 *  stand, and the browser lands on the entry that already says it, which the `hashchange` listener
 *  reads. */
export function navigate(hash: string, step: PlaceStep = "push"): void {
  if (step === "back") {
    history.back();
    return;
  }
  if (step === "replace") history.replaceState(null, "", hash);
  else if (location.hash !== hash) location.hash = hash;
  publish(arrive(parseHash(hash), heldRoute()));
}

const readAddress = () => publish(arrive(parseHash(location.hash), heldRoute()));

/** Start the router: state the boot address in the bar, land the arrival, and follow the browser
 *  from there. Returns the detach for the shell's own unmount. */
export function startRouter(): () => void {
  /* An artifact named by query is not a screen — a sign-in carried a signed file link through, and
     the browser goes to the file itself. */
  const target = artifactTarget(location.search);
  if (target) location.replace(target);
  const booted = heldRoute();
  /* A conversation and the first run are reached by query too, since a fragment never reaches the
     server. The address states either of them from here on, so what the member sees is somewhere
     they can return to and send on. */
  if (!location.hash) {
    if (booted.kind === "chat") history.replaceState(null, "", chatHash(booted.conversationId));
    if (booted.kind === "first-run") history.replaceState(null, "", FIRST_RUN_HASH);
  }
  publish(arrive(booted, null));
  window.addEventListener("hashchange", readAddress);
  return () => {
    window.removeEventListener("hashchange", readAddress);
    held = null;
  };
}

export function openHome(): void {
  navigate(HOME_HASH);
}

export function openChat(conversationId: string): void {
  navigate(chatHash(conversationId));
}

/** A conversation with one of its own slots standing open, which is how a chat pane opens its
 *  artifacts and closes them again. */
export function openSlot(conversationId: string, slot: string | null): void {
  navigate(chatHash(conversationId, slot ?? undefined));
}

export function openNewChat(agentId: string): void {
  navigate(newChatHash(agentId));
}

export function openAgents(): void {
  navigate(AGENTS_HASH);
}

/** Where an unbacked wizard address forwards: the same screen, written over the address rather than
 *  stacked on it, so Back does not land on the forwarder again. */
export function forwardAgents(): void {
  navigate(AGENTS_HASH, "replace");
}

export function openBuilder(): void {
  navigate(BUILDER_HASH);
}

export function openAgent(agentId: string): void {
  navigate(agentHash(agentId));
}

/** An agent opened at a place rather than at its head — what the bare apps hash, which shows the
 *  main agent without having navigated to it, turns a place change into. */
export function openAgentPlace(agentId: string, place: WorkspacePlace): void {
  navigate(agentHash(agentId, place));
}

/** A place change on a screen: pushed from anywhere, but replaced or unwound only by the screen the
 *  member is standing on — a pane reporting a place it was already leaving must not rewrite where
 *  they went. */
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

export function resetRouter(): void {
  held = null;
}
