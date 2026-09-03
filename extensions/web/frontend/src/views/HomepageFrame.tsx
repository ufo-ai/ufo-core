import { useCallback, useEffect, useRef, useState } from "react";

import { attachBridge, type BridgeHandle } from "@/lib/bridge";
import { usePanelRead } from "@/kernel/panel";
import { agentName } from "@/lib/agentName";
import { cn } from "@/lib/cn";
import { CHAT_SURFACE, useAgents } from "@/lib/mainAgent";
import { serializePlace, type WorkspacePlace } from "@/lib/route";
import { agentCrumb } from "@/lib/title";
import type { Agent, Homepage, Member } from "@/lib/types";

/** How long the arriving frame takes to fade over the one it replaces, and how long after that the
 *  replaced frame is kept mounted under it. The page it swaps in has already fired `load`, so the
 *  fade is the whole wait — nothing here delays the swap past the paint it exists to smooth. */
const FRAME_SWAP_MS = 200;
const HOMEPAGE_POLL_MS = 30_000;

const HOMEPAGE_SANDBOX =
  "allow-scripts allow-same-origin allow-forms allow-popups allow-modals allow-downloads allow-pointer-lock";

/** One mounted copy of the homepage frame. A redeploy or ended session remounts the page at the
 *  same URL, and a frame torn down the instant its successor mounts leaves the member watching
 *  the successor's blank document paint — so the standing copy holds the screen until the
 *  arriving one has loaded, and the two cross-fade. `key` is the URL, deploy generation, and
 *  session refresh together, the identity the frame remounts on. */
type HeldFrame = { key: string; url: string; loaded: boolean };

/** An app's homepage as a screen holds it: the boot read's answer at once, kept live by the poll
 *  that begins after its first interval. `settles` re-reads it on an act taken beside the page. */
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

/** The sandboxed frame an app's homepage renders in, wherever the portal draws one. Each frame's
 *  key carries the deploy generation, so a redeploy at the same URL mounts a fresh copy rather
 *  than showing the page the member last loaded — arriving invisible over the standing one and
 *  fading in on its own load, so the swap never paints the blank document. The box and the frames
 *  wear the surrounding surface's background and the portal's color-scheme, so what shows through
 *  an empty frame is the portal rather than a browser's white canvas.
 *
 *  The bridge is how the framed page reads the member's data and drives navigation; it is bound to
 *  the live frame and rebound when a redeploy remounts it under a new key, so each set of bytes
 *  talks to exactly one listener. The place rides `init` on a fresh frame and the bridge's own
 *  `place` message while the frame stands, because an arrival can land on a page that already
 *  booted. `onFounded` is a conversation the page's own send founded, with the agent it ran under
 *  — the page chats across agents, so the frame's own agent cannot stand in. `banded` is whether
 *  the frame stands under a lane band that already names the page and holds the way out; it rides
 *  `init`, so every header inside the page is the acts it carries and nothing more.
 *
 *  The bridge is rebound on the frame's identity alone. What the page is told about the member, the
 *  roster and its own step of the trail is read through a ref when it asks, because a re-read while
 *  the frame stands — the boot reload, a status poll, a rename the read corrects — arrives as new
 *  values, and a rebind on them would abort every stream the page holds open, across every frame
 *  home mounts at once. */
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
  /** Where a conversation the page links to opens. A lane states it, so the transcript stands in a
   *  lane beside the page; a frame holding the whole screen states none and the permalink stands. */
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
  // The frames this box is holding: the page showing, and — through a redeploy — the copy arriving
  // under the new generation. Reconciled in render rather than an effect so the arriving frame
  // mounts in the same commit that moves the bridge's dependencies, which is what points `frameRef`
  // at it before the bridge attaches. At most two stand at once: the last loaded copy and the one
  // arriving, so a redeploy racing another drops the copy that never showed.
  const [frames, setFrames] = useState<HeldFrame[]>([]);
  const frameKey = url + ":" + generation + ":" + frameRefresh;
  if (frames.at(-1)?.key !== frameKey) {
    setFrames([
      ...frames.filter((frame) => frame.loaded).slice(-1),
      { key: frameKey, url, loaded: false },
    ]);
  }
  // A load promotes only the newest frame: one from a copy already being replaced would fade in
  // bytes the next deploy has superseded. The replaced frame unmounts once the fade is over, and a
  // frame that never loads leaves it standing — the member keeps the page they had.
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
