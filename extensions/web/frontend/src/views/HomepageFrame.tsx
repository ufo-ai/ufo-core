import { useCallback, useEffect, useRef, useState } from "react";

import { attachBridge, type BridgeHandle } from "@/lib/bridge";
import { usePanelRead } from "@/kernel/panel";
import { agentName } from "@/lib/agentName";
import { cn } from "@/lib/cn";
import { CHAT_SURFACE, useAgents } from "@/lib/mainAgent";
import { serializePlace, type WorkspacePlace } from "@/lib/route";
import { agentCrumb } from "@/lib/title";
import type { Agent, Homepage, Member } from "@/lib/types";

const FRAME_SWAP_MS = 200;
const HOMEPAGE_POLL_MS = 30_000;

const HOMEPAGE_SANDBOX =
  "allow-scripts allow-same-origin allow-forms allow-popups allow-modals allow-downloads allow-pointer-lock";

type HeldFrame = { key: string; url: string; loaded: boolean };

export function useHomepage(agent: Agent, settles: number = 0): Homepage {
  const boot: Homepage = agent.homepage ?? { state: "none" };
  const [polling, setPolling] = useState(false);
  useEffect(() => {
    const start = window.setTimeout(() => setPolling(true), HOMEPAGE_POLL_MS);
    return () => window.clearTimeout(start);
  }, []);
  const site = usePanelRead<Homepage>(
    polling ? "/agents/" + agent.id + "/homepage" : null,
    settles,
    HOMEPAGE_POLL_MS,
  );
  return site.phase === "ready" ? site.payload : boot;
}

export function HomepageFrame({
  agent,
  member,
  url,
  generation,
  place,
  banded,
  onFounded,
  onConversation,
}: {
  agent: Agent;
  member: Member;
  url: string;
  generation: number;
  place: WorkspacePlace;
  banded: boolean;
  onFounded: (agent: Agent, conversationId: string, title: string) => void;
  onConversation?: (conversationId: string) => void;
}) {
  const agents = useAgents();
  const standing = { member, agents, crumb: agentCrumb(agent) };
  const standingRef = useRef(standing);
  standingRef.current = standing;
  const [frameRefresh, setFrameRefresh] = useState(0);
  const sessionEnded = useRef(false);
  const refreshEndedSession = useCallback(() => {
    if (!sessionEnded.current) return;
    sessionEnded.current = false;
    setFrameRefresh((value) => value + 1);
  }, []);
  useEffect(() => {
    window.addEventListener("focus", refreshEndedSession);
    return () => window.removeEventListener("focus", refreshEndedSession);
  }, [refreshEndedSession]);
  const frameRef = useRef<HTMLIFrameElement>(null);
  const bridgeRef = useRef<BridgeHandle | null>(null);
  const placeRef = useRef(place);
  placeRef.current = place;
  const placedAt = serializePlace(place);
  const [frames, setFrames] = useState<HeldFrame[]>([]);
  const frameKey = url + ":" + generation + ":" + frameRefresh;
  if (frames.at(-1)?.key !== frameKey) {
    setFrames([
      ...frames.filter((frame) => frame.loaded).slice(-1),
      { key: frameKey, url, loaded: false },
    ]);
  }
  const landed = (key: string) => {
    setFrames((held) =>
      held.at(-1)?.key === key
        ? held.map((frame) => (frame.key === key ? { ...frame, loaded: true } : frame))
        : held,
    );
    window.setTimeout(() => {
      setFrames((held) =>
        held.at(-1)?.key === key && held.at(-1)?.loaded ? held.slice(-1) : held,
      );
    }, FRAME_SWAP_MS);
  };
  const foundedRef = useRef<(agentId: string, conversationId: string, title: string) => void>(
    () => {},
  );
  foundedRef.current = (agentId, conversationId, title) => {
    const speaking = agents.find((entry) => entry.id === agentId);
    if (speaking) onFounded(speaking, conversationId, title);
  };
  const conversationRef = useRef(onConversation);
  conversationRef.current = onConversation;
  const lanes = onConversation !== undefined;
  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const handle = attachBridge({
      iframe: frame,
      standing: () => standingRef.current,
      agentId: agent.id,
      banded,
      place: placeRef.current,
      chatSurface: agent.app === CHAT_SURFACE,
      onCreated: (agentId, conversationId, title) =>
        foundedRef.current(agentId, conversationId, title),
      ...(lanes
        ? { onConversation: (conversationId: string) => conversationRef.current?.(conversationId) }
        : {}),
      onSessionEnded: () => {
        sessionEnded.current = true;
        if (document.hasFocus()) refreshEndedSession();
      },
    });
    bridgeRef.current = handle;
    return () => {
      bridgeRef.current = null;
      handle.detach();
    };
  }, [agent.id, agent.app, url, generation, frameRefresh, banded, lanes, refreshEndedSession]);
  useEffect(() => {
    bridgeRef.current?.place(placeRef.current);
  }, [placedAt]);
  return (
    <div className="relative min-h-0 flex-1 bg-surface">
      {frames.map((frame, index) => (
        <iframe
          key={frame.key}
          ref={index === frames.length - 1 ? frameRef : undefined}
          src={frame.url}
          title={agentName(agent.name) + " homepage"}
          sandbox={HOMEPAGE_SANDBOX}
          referrerPolicy="no-referrer"
          allow="fullscreen"
          onLoad={() => landed(frame.key)}
          className={cn(
            "absolute inset-0 size-full border-0 bg-surface",
            "transition-opacity duration-200 ease-out [color-scheme:inherit]",
            frame.loaded ? "opacity-100" : "pointer-events-none opacity-0",
          )}
        />
      ))}
    </div>
  );
}
