import { StrictMode, useCallback, useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { createRoot } from "react-dom/client";

import { connect, installShims, navigate, onPlaced, type AppInit } from "@/apps/runtime";
import { TooltipProvider } from "@/components/ui/tooltip";
import { BASE, getJson } from "@/lib/api";
import { Viewer } from "@/lib/audience";
import { MainAgentProvider } from "@/lib/mainAgent";
import {
  agentHash,
  framedNavigation,
  parseHash,
  serializePlace,
  type PlaceStep,
  type Route,
  type WorkspacePlace,
} from "@/lib/route";
import type { Agent, AgentsPayload } from "@/lib/types";
import { TabbedPane } from "@/views/TabbedPane";
import type { PaneView } from "@/views/registry";


/** An app homepage is the portal's own screen mounted alone: the view, its registry entry, and the
 *  section host are the code the portal ran when the screen was built in, so the page is the
 *  reference rendering rather than a copy of it — the runtime shims are the only seam. Links the
 *  page cannot claim as its own place ride the bridge's navigate verb; the frame's address never
 *  moves. */

/** Route every in-page link: a route the app claims changes its own place; anything else the shell
 *  may take goes over the bridge. Either way the click never mutates the frame's address. */
export function useAppLinks(claim: (route: Route) => boolean, portal: string): void {
  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.button !== 0) return;
      const anchor = (event.target as Element | null)?.closest?.("a[href]");
      const stated = anchor?.getAttribute("href");
      const address = stated && !stated.startsWith("#/") ? new URL(stated, location.href) : null;
      const to =
        stated?.startsWith("#/") ||
        (address?.origin === portal &&
          address.pathname === BASE &&
          framedNavigation(address.hash))
          ? (address?.hash ?? stated)
          : null;
      if (!to) return;
      event.preventDefault();
      if (claim(parseHash(to))) return;
      if (framedNavigation(to)) navigate(to);
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, [claim, portal]);
}

/** One section screen standing as the whole page, its place in page state and seeded by the place the
 *  pane was opened at — the whole place, so the screen inside the frame stands where the address
 *  outside it says. A link back into the same section is a place change here, never a trip through
 *  the portal. */
export function SectionApp({
  tab,
  view,
  init,
}: {
  tab: string;
  view: PaneView;
  init: AppInit;
}) {
  const [place, setPlace] = useState<WorkspacePlace>(init.place);
  const onPlace = useCallback(
    (_tab: string, next: WorkspacePlace, _step: PlaceStep) => {
      setPlace(next);
      navigate(agentHash(init.agentId, next));
    },
    [init.agentId],
  );
  // The pane's own place changes are taken as they arrive, and one the page itself just reported is
  // not taken twice: the address is what says two places are the same place.
  useEffect(
    () =>
      onPlaced((next) =>
        setPlace((held) => (serializePlace(held) === serializePlace(next) ? held : next)),
      ),
    [],
  );
  useAppLinks(
    useCallback(
      (route) => {
        if (route.kind !== "section" || route.section !== tab) return false;
        setPlace(route.place);
        navigate(agentHash(init.agentId, route.place));
        return true;
      },
      [tab, init.agentId],
    ),
    init.portal,
  );
  return (
    <div
      className="contents"
      style={
        {
          "--pane-acts-inset":
            "calc(2 * var(--size-control) + var(--spacing-xs) + var(--spacing-lg))",
        } as CSSProperties
      }
    >
      <TabbedPane
        group="section"
        tabs={[tab]}
        views={{ [tab]: view }}
        view={tab}
        place={place}
        onPlace={onPlace}
      />
    </div>
  );
}

function Booted({
  init,
  render,
}: {
  init: AppInit;
  render: (init: AppInit, agents: Agent[]) => ReactNode;
}) {
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  useEffect(() => {
    getJson<AgentsPayload>("/api/agents").then((answer) => {
      if (answer.ok) setAgents(answer.payload.agents);
      else setFailed(answer.message);
    });
  }, []);
  if (failed) return <p className="p-4 text-sm">{failed}</p>;
  if (agents === null) return null;
  // The page is the whole viewport, hosted the way the portal's shell hosts a pane: one grid cell
  // the height of the screen, stretching what stands in it. The screens size themselves by growing
  // into a bounded column, and a page as tall as its content would stack a start screen at the top
  // and let a transcript push its composer off the bottom.
  return (
    <TooltipProvider>
      <Viewer.Provider value={init.member.email}>
        <MainAgentProvider agents={agents}>
          <div className="grid h-dvh min-h-0">{render(init, agents)}</div>
        </MainAgentProvider>
      </Viewer.Provider>
    </TooltipProvider>
  );
}

export function mountApp(
  root: HTMLElement,
  render: (init: AppInit, agents: Agent[]) => ReactNode,
): void {
  void connect().then((init) => {
    installShims();
    createRoot(root).render(
      <StrictMode>
        <Booted init={init} render={render} />
      </StrictMode>,
    );
  });
}
