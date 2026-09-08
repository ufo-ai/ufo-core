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
      if (!init.banded) navigate(agentHash(init.agentId, next));
    },
    [init.agentId, init.banded],
  );
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
