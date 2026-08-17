import { useState } from "react";

import { PanelEmpty, PanelSkeleton, usePanelRead } from "@/kernel/panel";
import { WEB_SURFACE, surfaceWord } from "@/lib/audience";
import { cn } from "@/lib/cn";
import { workspaceHash } from "@/lib/route";
import type { Agent, Subagent } from "@/lib/types";
import type { PoolConnection, PoolPayload } from "@/views/Connectors";
import type { Source, SourcesPayload } from "@/views/Sources";

type Installation = { surface: string; agent_id: string };
type SurfacesPayload = { installations: Installation[] };

const LEFT_W = 190;
const AGENT_W = 200;
const RIGHT_W = 230;
const COL_GAP = 110;
const LEFT_X = 0;
const AGENT_X = LEFT_W + COL_GAP;
const RIGHT_X = AGENT_X + AGENT_W + COL_GAP;
const WIDTH = RIGHT_X + RIGHT_W;

const SURFACE_H = 36;
const AGENT_H = 56;
const PROVIDER_H = 44;
const TILE_HEADER_H = 30;
const TILE_ROW_H = 26;
const TILE_PAD = 6;
const NODE_GAP = 18;
const PROVIDER_GAP = 10;
const CURVE = 55;
const PORT_PAD = 10;

const FADE = "transition-opacity duration-100 motion-reduce:transition-none";
const CAPSULE = cn("absolute flex items-center justify-center rounded-full bg-fill text-label", FADE);
const CARD = cn(
  "absolute flex flex-col justify-center gap-2xs rounded-control border border-edge bg-surface px-lg",
  FADE,
);
const TILE = cn("absolute overflow-hidden rounded-control border border-edge bg-surface", FADE);
const ROW = cn(
  "flex w-full items-center border-0 bg-transparent px-lg text-left text-label text-inherit hover:bg-fill",
  FADE,
);
const HEADER = "flex items-center px-lg text-small text-ink-soft";
const DIM = "opacity-40";
const IDLE_LEAD = 0.5;
const LIT_LEAD = 0.9;
const FADED_LEAD = 0.12;

type Box = { x: number; y: number; w: number; h: number };
type EdgeKind = "surface" | "memory" | "connector" | "subagent";
type Wire = { kind: EdgeKind; from: string; to: string };
type Edge = Wire & { x1: number; y1: number; x2: number; y2: number };

type Provider = { provider: string; connections: PoolConnection[] };

type Layout = {
  height: number;
  boxes: Map<string, Box>;
  surfaces: string[];
  providers: Provider[];
  sourceLabels: string[];
  edges: Edge[];
  neighbors: Map<string, Set<string>>;
};

const MEMORY = "memory";
const SUBAGENTS = "subagents";

function surfaceKey(name: string): string {
  return "surface:" + name;
}

function agentKey(id: string): string {
  return "agent:" + id;
}

function providerKey(name: string): string {
  return "provider:" + name;
}

function tileHeight(rows: number): number {
  return TILE_HEADER_H + rows * TILE_ROW_H + TILE_PAD;
}

/** Edge endpoints leave a node through ports spread along its side, ordered by where the far end
 *  sits, so a fan stays readable instead of pinching into one point. */
function ports(count: number, at: Box): number[] {
  if (count === 1) return [at.y + at.h / 2];
  const top = at.y + PORT_PAD;
  const step = (at.h - 2 * PORT_PAD) / (count - 1);
  return Array.from({ length: count }, (_, index) => top + index * step);
}

function build(
  agents: Agent[],
  subagents: Subagent[],
  installations: Installation[],
  connections: PoolConnection[],
  sources: Source[],
): Layout {
  const surfaces = [
    WEB_SURFACE,
    ...[...new Set(installations.map((entry) => entry.surface))]
      .filter((name) => name !== WEB_SURFACE)
      .sort(),
  ];
  const sourceLabels = [...new Set(sources.map((entry) => entry.name ?? entry.backend))];
  const byProvider = new Map<string, PoolConnection[]>();
  for (const connection of connections) {
    byProvider.set(connection.provider, [...(byProvider.get(connection.provider) ?? []), connection]);
  }
  const providers = [...byProvider.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([provider, held]) => ({ provider, connections: held }));

  const boxes = new Map<string, Box>();
  let y = 0;
  for (const name of surfaces) {
    boxes.set(surfaceKey(name), { x: LEFT_X, y, w: LEFT_W, h: SURFACE_H });
    y += SURFACE_H + NODE_GAP;
  }
  boxes.set(MEMORY, { x: LEFT_X, y, w: LEFT_W, h: tileHeight(sourceLabels.length) });
  const leftH = y + tileHeight(sourceLabels.length);

  y = 0;
  for (const agent of agents) {
    boxes.set(agentKey(agent.id), { x: AGENT_X, y, w: AGENT_W, h: AGENT_H });
    y += AGENT_H + NODE_GAP;
  }
  const centerH = Math.max(0, y - NODE_GAP);

  y = 0;
  for (const entry of providers) {
    boxes.set(providerKey(entry.provider), { x: RIGHT_X, y, w: RIGHT_W, h: PROVIDER_H });
    y += PROVIDER_H + PROVIDER_GAP;
  }
  if (subagents.length) {
    y += providers.length ? NODE_GAP - PROVIDER_GAP : 0;
    boxes.set(SUBAGENTS, { x: RIGHT_X, y, w: RIGHT_W, h: tileHeight(subagents.length) });
    y += tileHeight(subagents.length);
  } else {
    y = Math.max(0, y - PROVIDER_GAP);
  }
  const rightH = y;

  const height = Math.max(leftH, centerH, rightH, SURFACE_H);
  for (const [key, at] of boxes) {
    const column = at.x === LEFT_X ? leftH : at.x === AGENT_X ? centerH : rightH;
    boxes.set(key, { ...at, y: at.y + (height - column) / 2 });
  }

  const bound = new Set(agents.map((agent) => WEB_SURFACE + "\n" + agent.id));
  for (const entry of installations) {
    if (boxes.has(agentKey(entry.agent_id))) bound.add(entry.surface + "\n" + entry.agent_id);
  }
  const wires: Wire[] = [];
  for (const name of surfaces) {
    for (const agent of agents) {
      if (!bound.has(name + "\n" + agent.id)) continue;
      wires.push({ kind: "surface", from: surfaceKey(name), to: agentKey(agent.id) });
    }
  }
  for (const agent of agents) {
    wires.push({ kind: "memory", from: MEMORY, to: agentKey(agent.id) });
  }
  const attached = new Set<string>();
  for (const entry of providers) {
    for (const connection of entry.connections) {
      for (const holder of connection.agents) {
        if (!boxes.has(agentKey(holder.id))) continue;
        attached.add(agentKey(holder.id) + "\n" + entry.provider);
      }
    }
  }
  for (const pair of [...attached].sort()) {
    const [from, provider] = pair.split("\n");
    wires.push({ kind: "connector", from, to: providerKey(provider) });
  }
  if (subagents.length) {
    for (const agent of agents) {
      wires.push({ kind: "subagent", from: agentKey(agent.id), to: SUBAGENTS });
    }
  }

  const center = (key: string) => {
    const at = boxes.get(key);
    if (!at) throw new Error("no box for node " + key);
    return at.y + at.h / 2;
  };
  const leaving = new Map<string, Wire[]>();
  const entering = new Map<string, Wire[]>();
  for (const wire of wires) {
    leaving.set(wire.from, [...(leaving.get(wire.from) ?? []), wire]);
    entering.set(wire.to, [...(entering.get(wire.to) ?? []), wire]);
  }
  const starts = new Map<Wire, number>();
  const ends = new Map<Wire, number>();
  for (const [key, held] of leaving) {
    const sorted = [...held].sort((a, b) => center(a.to) - center(b.to));
    const at = boxes.get(key);
    if (!at) continue;
    ports(sorted.length, at).forEach((port, index) => starts.set(sorted[index], port));
  }
  for (const [key, held] of entering) {
    const sorted = [...held].sort((a, b) => center(a.from) - center(b.from));
    const at = boxes.get(key);
    if (!at) continue;
    ports(sorted.length, at).forEach((port, index) => ends.set(sorted[index], port));
  }
  const edges = wires.map((wire) => {
    const from = boxes.get(wire.from);
    const to = boxes.get(wire.to);
    if (!from || !to) throw new Error("edge without a node");
    return {
      ...wire,
      x1: from.x + from.w,
      y1: starts.get(wire) ?? from.y + from.h / 2,
      x2: to.x,
      y2: ends.get(wire) ?? to.y + to.h / 2,
    };
  });

  const neighbors = new Map<string, Set<string>>();
  for (const key of boxes.keys()) neighbors.set(key, new Set([key]));
  for (const wire of wires) {
    neighbors.get(wire.from)?.add(wire.to);
    neighbors.get(wire.to)?.add(wire.from);
  }

  return { height, boxes, surfaces, providers, sourceLabels, edges, neighbors };
}

function lead(edge: Edge): string {
  return (
    `M${edge.x1} ${edge.y1} ` +
    `C${edge.x1 + CURVE} ${edge.y1}, ${edge.x2 - CURVE} ${edge.y2}, ${edge.x2} ${edge.y2}`
  );
}

function place(at: Box) {
  return { left: at.x, top: at.y, width: at.w, height: at.h };
}

export function AgentGraph({
  agents,
  subagents,
  query,
  reloads,
  onOpen,
  onOpenSubagent,
}: {
  agents: Agent[];
  subagents: Subagent[];
  query: string;
  reloads: number;
  onOpen: (agentId: string) => void;
  onOpenSubagent: (name: string) => void;
}) {
  const [focus, setFocus] = useState<string | null>(null);
  const surfaces = usePanelRead<SurfacesPayload>("/workspace/surfaces", reloads);
  const pool = usePanelRead<PoolPayload>("/connections", reloads);
  const sources = usePanelRead<SourcesPayload>("/workspace/sources", reloads);
  const fault = [surfaces, pool, sources].find((read) => read.phase === "failed");
  if (fault?.phase === "failed") return <PanelEmpty>{fault.message}</PanelEmpty>;
  if (surfaces.phase !== "ready" || pool.phase !== "ready" || sources.phase !== "ready") {
    return <PanelSkeleton shape="cards" />;
  }

  const layout = build(
    agents,
    subagents,
    surfaces.payload.installations,
    pool.payload.connections,
    sources.payload.sources,
  );
  const wanted = query.trim().toLowerCase();
  const unsaid = (said: string) => Boolean(wanted) && !said.toLowerCase().includes(wanted);
  const away = (key: string) => focus !== null && !layout.neighbors.get(focus)?.has(key);
  const held = (key: string) => ({
    onMouseEnter: () => setFocus(key),
    onMouseLeave: () => setFocus(null),
    onFocus: () => setFocus(key),
    onBlur: () => setFocus(null),
  });
  const at = (key: string) => {
    const box = layout.boxes.get(key);
    if (!box) throw new Error("no box for node " + key);
    return place(box);
  };

  return (
    <div className="overflow-x-auto py-2xl">
      <div
        role="group"
        aria-label="Agent topology"
        className="relative"
        style={{ width: WIDTH, height: layout.height }}
      >
        <svg
          aria-hidden
          width={WIDTH}
          height={layout.height}
          className="pointer-events-none absolute inset-0 text-edge-strong"
        >
          {layout.edges.map((edge, index) => (
            <path
              key={index}
              data-edge={edge.kind}
              d={lead(edge)}
              fill="none"
              stroke="currentColor"
              strokeOpacity={
                focus === null ? IDLE_LEAD : focus === edge.from || focus === edge.to ? LIT_LEAD : FADED_LEAD
              }
            />
          ))}
        </svg>
        {layout.surfaces.map((name) => (
          <div
            key={name}
            data-node
            {...held(surfaceKey(name))}
            className={cn(CAPSULE, (unsaid(surfaceWord(name)) || away(surfaceKey(name))) && DIM)}
            style={at(surfaceKey(name))}
          >
            {surfaceWord(name)}
          </div>
        ))}
        {agents.map((agent) => (
          <button
            key={agent.id}
            type="button"
            data-node
            {...held(agentKey(agent.id))}
            onClick={() => onOpen(agent.id)}
            className={cn(
              CARD,
              "text-left hover:border-edge-strong",
              agent.main && "border-edge-strong",
              (unsaid(agent.name) || away(agentKey(agent.id))) && DIM,
            )}
            style={at(agentKey(agent.id))}
          >
            <span className="flex items-baseline gap-sm">
              <span className="min-w-0 truncate text-label">{agent.name}</span>
              {agent.main ? <span className="text-small text-ink-soft">Main</span> : null}
            </span>
            <span className="truncate font-mono text-small text-ink-soft">{agent.model}</span>
          </button>
        ))}
        {layout.providers.map((entry) => {
          const one = entry.connections.length === 1 ? entry.connections[0] : null;
          const detail = one
            ? (one.account_label ?? one.account_id)
            : entry.connections.length + " accounts";
          const said = [
            entry.provider,
            ...entry.connections.flatMap((connection) => [
              connection.account_label,
              connection.account_id,
            ]),
          ]
            .filter(Boolean)
            .join(" ");
          return (
            <a
              key={entry.provider}
              data-node
              {...held(providerKey(entry.provider))}
              href={workspaceHash("connectors")}
              className={cn(
                CARD,
                "text-inherit no-underline hover:border-edge-strong",
                (unsaid(said) || away(providerKey(entry.provider))) && DIM,
              )}
              style={at(providerKey(entry.provider))}
            >
              <span className="truncate text-label">{entry.provider}</span>
              {detail ? (
                <span className="truncate font-mono text-small text-ink-soft">{detail}</span>
              ) : null}
            </a>
          );
        })}
        <div
          data-node
          {...held(MEMORY)}
          className={cn(TILE, away(MEMORY) && DIM)}
          style={at(MEMORY)}
        >
          <a
            href={workspaceHash("memory")}
            className={cn(HEADER, "text-inherit no-underline hover:bg-fill")}
            style={{ height: TILE_HEADER_H }}
          >
            Memory
          </a>
          {layout.sourceLabels.map((label) => (
            <a
              key={label}
              href={workspaceHash("sources")}
              className={cn(ROW, "no-underline", unsaid(label) && DIM)}
              style={{ height: TILE_ROW_H }}
            >
              <span className="min-w-0 truncate">{label}</span>
            </a>
          ))}
        </div>
        {subagents.length ? (
          <div
            data-node
            {...held(SUBAGENTS)}
            className={cn(TILE, away(SUBAGENTS) && DIM)}
            style={at(SUBAGENTS)}
          >
            <div className={HEADER} style={{ height: TILE_HEADER_H }}>
              Subagents
            </div>
            {subagents.map((subagent) => (
              <button
                key={subagent.name}
                type="button"
                onClick={() => onOpenSubagent(subagent.name)}
                className={cn(ROW, unsaid(subagent.name) && DIM)}
                style={{ height: TILE_ROW_H }}
              >
                <span className="min-w-0 truncate">{subagent.name}</span>
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
