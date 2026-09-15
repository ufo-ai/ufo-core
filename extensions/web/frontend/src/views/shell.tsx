import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import {
  IconBook,
  IconDeviceDesktop,
  IconLogout,
  IconMenu2,
  IconMoon,
  IconSun,
  IconX,
} from "@tabler/icons-react";

import logo from "@/assets/ufo-logo.svg";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useDrawerSlot } from "@/kernel/drawer";
import { COLUMN, Pane } from "@/kernel/pane";
import { Loading } from "@/kernel/panel";
import { SIGN_OUT_PATH } from "@/lib/api";
import { useAppStatus } from "@/lib/appStatusStore";
import { Me, Viewer, WorkspaceId } from "@/lib/audience";
import { cn } from "@/lib/cn";
import { MainAgentProvider } from "@/lib/mainAgent";
import { deployment, type Deployment } from "@/lib/mark";
import { useNarrow } from "@/lib/narrow";
import { readRail, seekChat, useRail, watchRail, type RailState } from "@/lib/railStore";
import type { Route } from "@/lib/route";
import { openHome, startRouter, useRoute, useTravel, type Fleeting } from "@/lib/router";
import { SCHEME_OPTIONS, pickScheme, useScheme, type Scheme } from "@/lib/scheme";
import { ALL_SURFACES, SurfacesProvider } from "@/lib/surfaces";
import { pageTitle } from "@/lib/title";
import type { Agent, ArchivedApp, Member, Surfaces } from "@/lib/types";
import { Spotlight } from "@/views/Spotlight";

export type AppProps = {
  agents: Agent[];
  archived?: ArchivedApp[];
  member: Member;
  surfaces?: Surfaces;
  onAgents: () => void;
};

export const GLYPH = "size-(--size-glyph) shrink-0";

export function PaneLoading() {
  return (
    <Pane className={COLUMN}>
      <Loading />
    </Pane>
  );
}

function inSetup(route: Route, agents: Agent[]): boolean {
  if (route.kind !== "agent" && route.kind !== "agent-setup") return false;
  return agents.some((agent) => agent.id === route.agentId && agent.stands_on_setup === true);
}

/** The boot read that seeded the shell predates the apps a workspace ships on its first turn. Each id
 *  is asked about once: a re-read that comes back without it must not send the next tick asking again. */
function useProvisioned(agents: Agent[], onAgents: () => void): void {
  const { statuses } = useAppStatus();
  const asked = useRef<Set<string>>(new Set());
  useEffect(() => {
    const known = new Set(agents.map((agent) => agent.id));
    const gained = Object.keys(statuses).filter((id) => !known.has(id) && !asked.current.has(id));
    if (!gained.length) return;
    for (const id of gained) asked.current.add(id);
    onAgents();
  }, [statuses, agents, onAgents]);
}

export const SCROLL_MARK = "data-scrolling";

/** It covers the pause between two wheel notches and the pause in the middle of a drag, so one gesture
 *  draws one bar, and is short enough that a pane the member left alone is quiet before their eye returns. */
export const SCROLL_QUIET_MS = 600;

/** `scroll` does not bubble but it does capture, so one listener at the document reaches every scroller
 *  the portal draws, including one mounted after this ran. */
function useScrollMark(): void {
  useEffect(() => {
    const quiet = new Map<Element, number>();
    const mark = (event: Event) => {
      const element = event.target;
      if (!(element instanceof Element)) return;
      const held = quiet.get(element);
      if (held === undefined) element.setAttribute(SCROLL_MARK, "");
      else clearTimeout(held);
      quiet.set(
        element,
        window.setTimeout(() => {
          quiet.delete(element);
          element.removeAttribute(SCROLL_MARK);
        }, SCROLL_QUIET_MS),
      );
    };
    document.addEventListener("scroll", mark, { capture: true, passive: true });
    return () => {
      document.removeEventListener("scroll", mark, { capture: true });
      for (const [element, held] of quiet) {
        clearTimeout(held);
        element.removeAttribute(SCROLL_MARK);
      }
    };
  }, []);
}

export type Shell = {
  route: Route;
  rail: RailState;
  narrow: boolean;
  menu: boolean;
  setMenu: (open: boolean) => void;
  shutMenu: () => void;
  mainAgent: Agent | null;
  listed: Agent[];
  /** The chrome stands around every screen but an app's own setup, which fills the page. */
  chrome: boolean;
};

/** What every shell does once it mounts, whatever it draws around the pane: it starts the router and
 *  the rail, closes its drawer when the member moves, titles the tab, and seeks the chat the address
 *  names. */
export function useShell(agents: Agent[], onAgents: () => void, fleeting?: Fleeting): Shell {
  const route = useRoute();
  const travel = useTravel();
  const rail = useRail();
  const [menu, setMenu] = useState(false);
  const shutMenu = useCallback(() => setMenu(false), []);
  const narrow = useNarrow();
  useScrollMark();
  const mainAgent = agents.find((agent) => agent.main) ?? agents[0] ?? null;
  /** A fresh array here re-runs every effect that depends on it — the palette's search aborts
   *  and re-fires on each status poll, which the member sees as a second search. */
  const listed = useMemo(() => agents.filter((agent) => !agent.hidden), [agents]);

  const held = useRef(fleeting);
  useEffect(() => startRouter({ fleeting: held.current }), []);

  useEffect(readRail, []);

  useEffect(watchRail, []);

  useProvisioned(agents, onAgents);

  /** Not `[route]`: a screen's `arrive` replaces the address on its first commit, which would shut a
   *  drawer the member had opened in those same frames. */
  useEffect(() => setMenu(false), [travel]);

  /** A drawer left open while the window grows past the breakpoint would trap focus behind a hamburger
   *  the layout no longer draws. */
  useEffect(() => {
    if (!narrow) setMenu(false);
  }, [narrow]);

  useEffect(() => {
    document.title = pageTitle(route, agents, rail.linked, mainAgent);
  }, [route, agents, rail.linked, mainAgent]);

  useEffect(() => {
    if (route.kind === "first-run" && !mainAgent) openHome();
  }, [mainAgent, route.kind]);

  const opened = route.kind === "chat" ? route.conversationId : null;
  useEffect(() => {
    if (opened !== null) seekChat(opened);
  }, [opened]);

  return {
    route,
    rail,
    narrow,
    menu,
    setMenu,
    shutMenu,
    mainAgent,
    listed,
    chrome: !inSetup(route, agents),
  };
}

export function ShellProviders({
  member,
  surfaces = ALL_SURFACES,
  agents,
  onAgents,
  children,
}: {
  member: Member;
  surfaces?: Surfaces;
  agents: Agent[];
  onAgents: () => void;
  children: ReactNode;
}) {
  return (
    <WorkspaceId.Provider value={member.workspace_id ?? null}>
      <Viewer.Provider value={member.email}>
        <Me.Provider value={member}>
          <SurfacesProvider surfaces={surfaces}>
            <MainAgentProvider agents={agents} onAgents={onAgents}>
              <TooltipProvider>{children}</TooltipProvider>
            </MainAgentProvider>
          </SurfacesProvider>
        </Me.Provider>
      </Viewer.Provider>
    </WorkspaceId.Provider>
  );
}

export function NarrowBar({
  agents,
  member,
  menu,
  onMenu,
}: {
  agents: Agent[];
  member: Member;
  menu: boolean;
  onMenu: (open: boolean) => void;
}) {
  return (
    <header className="relative flex items-center gap-md border-b border-edge bg-sidebar px-lg py-md">
      <button
        type="button"
        aria-label="Menu"
        aria-expanded={menu}
        onClick={() => onMenu(true)}
        className="flex size-(--size-control) shrink-0 items-center justify-center rounded-full border-0 bg-transparent p-0 text-inherit hover:bg-fill"
      >
        <IconMenu2 className="size-(--size-glyph)" aria-hidden />
      </button>
      <button
        type="button"
        aria-label="ufo"
        onClick={openHome}
        className="absolute start-1/2 -translate-x-1/2 border-0 bg-transparent p-0 text-inherit rtl:translate-x-1/2"
      >
        <span
          role="img"
          aria-label="ufo"
          className="block h-(--size-wordmark) w-(--size-logo) bg-current"
          style={{ mask: `url(${logo}) center / contain no-repeat` }}
        />
      </button>
      <Spotlight
        agents={agents}
        className="ml-auto flex h-(--size-row) items-center rounded-full border-0 bg-transparent px-md text-inherit hover:bg-fill"
      />
      <AccountMenu member={member} />
      <NavDrawer open={menu} onClose={() => onMenu(false)} />
    </header>
  );
}

function NavDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const hold = useDrawerSlot();
  return (
    <DialogPrimitive.Root open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-10 bg-scrim animate-appear" />
        <DialogPrimitive.Content
          data-slot="nav-drawer"
          aria-describedby={undefined}
          className={cn(
            "fixed inset-y-0 left-0 z-10 w-sidebar overflow-y-auto",
            "bg-sidebar border-r border-edge p-lg",
            "flex flex-col gap-2xl animate-slide-in",
          )}
        >
          <header className="flex h-(--size-control) shrink-0 items-center gap-md">
            <DialogPrimitive.Title asChild>
              <span
                role="img"
                aria-label="ufo"
                className="h-(--size-wordmark) w-(--size-logo) shrink-0 bg-current"
                style={{ mask: `url(${logo}) center / contain no-repeat` }}
              />
            </DialogPrimitive.Title>
            <DialogPrimitive.Close asChild>
              <Button size="icon" className="ml-auto" aria-label="Close">
                <IconX aria-hidden />
              </Button>
            </DialogPrimitive.Close>
          </header>
          <div ref={hold} className="flex min-h-0 flex-1 flex-col" />
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

export function signOut(): void {
  window.location.assign(SIGN_OUT_PATH);
}

const DOCUMENTATION = "Documentation";
const DOCUMENTATION_URLS: Record<Deployment, string> = {
  production: "https://ufo.ai/docs/",
  testing: "https://testing.ufo.ai/docs/",
  local: "https://ufo.ai/docs/",
};
const THEME = "Theme";
const SIGN_OUT = "Sign out";

const MENU_ITEM = "flex items-center gap-sm";

export function SchemeGlyph({ scheme }: { scheme: Scheme }) {
  if (scheme === "light") return <IconSun className={GLYPH} aria-hidden />;
  if (scheme === "dark") return <IconMoon className={GLYPH} aria-hidden />;
  return <IconDeviceDesktop className={GLYPH} aria-hidden />;
}

/** The header's avatar on a narrow viewport and a shell's own account row read this one menu, so
 *  `container` follows the menu these acts stand in rather than the document. */
export function AccountActs({ container }: { container?: HTMLElement | null }) {
  const scheme = useScheme();
  return (
    <>
      <DropdownMenuItem asChild>
        <a
          href={DOCUMENTATION_URLS[deployment(location.hostname)]}
          target="_blank"
          rel="noopener noreferrer"
          className="text-inherit no-underline"
        >
          <span className={MENU_ITEM}>
            <IconBook className={GLYPH} aria-hidden />
            {DOCUMENTATION}
          </span>
        </a>
      </DropdownMenuItem>
      <DropdownMenuSeparator />
      <DropdownMenuSub>
        <DropdownMenuSubTrigger>
          <span className={MENU_ITEM}>
            <SchemeGlyph scheme={scheme} />
            {THEME}
          </span>
        </DropdownMenuSubTrigger>
        <DropdownMenuSubContent container={container}>
          <DropdownMenuRadioGroup value={scheme} onValueChange={pickScheme}>
            {SCHEME_OPTIONS.map((option) => (
              <DropdownMenuRadioItem key={option.scheme} value={option.scheme}>
                {option.label}
              </DropdownMenuRadioItem>
            ))}
          </DropdownMenuRadioGroup>
        </DropdownMenuSubContent>
      </DropdownMenuSub>
      <DropdownMenuSeparator />
      <DropdownMenuItem onSelect={signOut}>
        <span className={MENU_ITEM}>
          <IconLogout className={GLYPH} aria-hidden />
          {SIGN_OUT}
        </span>
      </DropdownMenuItem>
    </>
  );
}

export function AccountMenu({ member }: { member: Member }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={member.email}
          className="flex shrink-0 items-center justify-center rounded-full border-0 bg-transparent p-0 max-narrow:size-(--size-control) data-[state=open]:outline data-[state=open]:outline-edge"
        >
          <Avatar>
            <AvatarFallback>{member.email.slice(0, 1).toUpperCase()}</AvatarFallback>
          </Avatar>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <div className="flex flex-col p-sm">
          <span className="truncate text-label">{member.email}</span>
        </div>
        <AccountActs />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
