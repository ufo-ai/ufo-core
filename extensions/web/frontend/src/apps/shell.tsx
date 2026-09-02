import {
  StrictMode,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { createRoot, type Root } from "react-dom/client";

import { connect, installShims, navigate, onPlaced, type AppInit } from "@/apps/runtime";
import {
  beginApplicationMount,
  finishApplicationStartup,
  markApplicationMounted,
  unmountApplication,
} from "@/apps/lifecycle";
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
 *  section host are the portal's own modules, so the page is the reference rendering rather than a
 *  copy of it — the runtime shims are the only seam. Links the page cannot claim as its own place
 *  ride the bridge's navigate verb; the frame's address never moves. */

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
 *  the portal.
 *
 *  Under a band the page's place is its own: the address the member holds names the lane, not the
 *  page's state. A lane hands the page no place to begin with, and a filter, a search, a page step
 *  or a record opened beside the listing is a move inside the lane — reported to the portal it would
 *  land the member on this page's own full screen, which is the track torn up to answer a press that
 *  never asked to leave it. So a banded place change is `setPlace` and nothing else, and the record
 *  lane it opens is drawn by the shell's own track inside the frame. Standing on its own screen the
 *  page reports every such change, because there the address is what the member holds.
 *
 *  This page's own app address is one of its links too — the crumb the shell hands a page standing
 *  one step deeper names it — so it is claimed the same way: pressed under a band it is the page
 *  coming back to its own head inside the lane, not the portal standing that head full screen.
 *
 *  A link out of this section is not a place change either way: it names an app or a section this
 *  page cannot draw, so it rides the bridge's navigate verb and the portal moves — banded too, where
 *  the lane is what the member left. */
export function SectionApp({
  tab,
  view,
  init,
}: {
  /** This page's one pane view, by name. Where the name is also a section the address codec reads,
   *  the page claims links to it; the rest are reached as their app, which the page claims by its
   *  own agent id. */
  tab: string;
  view: PaneView;
  init: AppInit;
}) {
  const [place, setPlace] = useState<WorkspacePlace>(init.place);
  const onPlace = useCallback(
    (_tab: string, next: WorkspacePlace, _step: PlaceStep) => {
      setPlace(next);
      if (!init.banded) navigate(agentHash(init.agentId, next));
    },
    [init.agentId, init.banded],
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
        const claimed =
          (route.kind === "section" && route.section === tab) ||
          (route.kind === "agent" && route.agentId === init.agentId);
        if (!claimed) return false;
        setPlace(route.place);
        if (!init.banded) navigate(agentHash(init.agentId, route.place));
        return true;
      },
      [tab, init.agentId, init.banded],
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
        banded={init.banded}
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
  const [agents, setAgents] = useState<Agent[] | null>(init.agents ?? null);
  const [failed, setFailed] = useState<string | null>(null);
  const requested = useRef(false);
  useEffect(() => {
    if (init.agents !== undefined || requested.current) return;
    requested.current = true;
    getJson<AgentsPayload>("/api/agents").then((answer) => {
      if (answer.ok) setAgents(answer.payload.agents);
      else setFailed(answer.message);
    });
  }, [init.agents]);
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

function ApplicationLifecycleBoundary({
  generation,
  children,
}: {
  generation: number;
  children: ReactNode;
}) {
  useLayoutEffect(() => markApplicationMounted(generation), [generation]);
  useEffect(() => finishApplicationStartup(generation), [generation]);
  return children;
}

/** Mount an app page: connects to the portal over the bridge, waits for its `init`, and renders
 * `render(init, agents)` into `root` inside the kit's providers. The one call a page's entry
 * makes. */
export function mountApp(
  root: HTMLElement,
  render: (init: AppInit, agents: Agent[]) => ReactNode,
): void {
  root.dataset.ufoApplication = "";
  const mounted = {
    active: true,
    generation: beginApplicationMount(root),
    root: null as Root | null,
  };
  mountedApps.set(root, mounted);
  void connect().then((init) => {
    if (!mounted.active) return;
    installShims();
    mounted.root = createRoot(root);
    mounted.root.render(
      <StrictMode>
        <ApplicationLifecycleBoundary generation={mounted.generation}>
          <Booted init={init} render={render} />
        </ApplicationLifecycleBoundary>
      </StrictMode>,
    );
  });
}

const mountedApps = new WeakMap<
  HTMLElement,
  { active: boolean; generation: number; root: Root | null }
>();

export function unmountApp(root: HTMLElement): void {
  const mounted = mountedApps.get(root);
  if (!mounted) return;
  mounted.active = false;
  mounted.root?.unmount();
  unmountApplication(mounted.generation);
  mountedApps.delete(root);
}
