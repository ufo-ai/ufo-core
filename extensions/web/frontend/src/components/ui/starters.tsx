import { IconChevronRight, IconPlug } from "@tabler/icons-react";

import logo from "@/assets/ufo-logo.svg";

import { AskMark } from "@/components/ui/offers";
import { PressRow, PRESS_ROW, PRESS_ROW_CHEVRON } from "@/components/ui/pressrow";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/cn";
import { sectionHash } from "@/lib/route";

export function Wordmark() {
  return (
    <span
      role="img"
      aria-label="ufo"
      className="mx-auto mb-8xl block h-(--size-wordmark-hero) w-(--size-logo-hero) bg-current max-narrow:hidden"
      style={{ mask: `url(${logo}) center / contain no-repeat` }}
    />
  );
}

export type StarterRow = {
  kind: "app" | "check_in" | "unlock";
  line: string;
  ask: string;
  agent_id?: string | null;
  providers?: MissingTile[];
};
export type MissingTile = { name: string; label: string };
export type UnlockRow = {
  line: string;
  ask: string;
  agent_id?: string | null;
  providers: MissingTile[];
};

const STARTER_PLACES = ["w-4/5", "w-3/5", "w-2/3"];

const STARTERS: { line: string; ask: string }[] = [
  {
    line: "Track the competitors you name, with a source for every claim.",
    ask: "I want an application that tracks the competitors I name and writes up what changed, with a source for each claim.",
  },
  {
    line: "Research a market, company, or person on request.",
    ask: "I want an application that researches a market, company, or person on request and cites every claim.",
  },
  {
    line: "Draft recurring updates, announcements, and posts.",
    ask: "I want an application that drafts our recurring updates, announcements, and posts.",
  },
];

export const FALLBACK_ROWS: StarterRow[] = STARTERS.map((starter) => ({ kind: "app", ...starter }));

function namedTiles(providers: MissingTile[]): string {
  const labels = providers.map((tile) => tile.label);
  return labels.length < 2
    ? labels.join("")
    : labels.slice(0, -1).join(", ") + " and " + labels.at(-1);
}

export function StarterWaiting({ width }: { width: string }) {
  return (
    <div
      data-part="starter-waiting"
      className={cn(PRESS_ROW, "pointer-events-none hover:bg-transparent")}
      aria-hidden
    >
      <Skeleton className="size-(--size-glyph) shrink-0" />
      <span className="relative min-w-0 flex-1">
        {"\u00a0"}
        <Skeleton className={cn("absolute inset-y-0 left-0", width)} />
      </span>
      <span className="size-(--size-glyph) shrink-0" />
    </div>
  );
}

export function Starters({
  rows,
  unlock,
  waiting,
  onStart,
}: {
  rows: StarterRow[];
  unlock: UnlockRow | null;
  waiting: boolean;
  onStart: (agentId: string | null | undefined, ask: string, kind: string) => void;
}) {
  return (
    <div className="mt-2xl flex flex-col">
      {waiting ? (
        STARTER_PLACES.map((width) => <StarterWaiting key={width} width={width} />)
      ) : (
        rows.map((row) => (
          <PressRow
            key={row.agent_id ?? `${row.kind}:${row.ask}`}
            glyph={<AskMark />}
            line={row.line}
            onPress={() => onStart(row.agent_id, row.ask, row.kind)}
          />
        ))
      )}
      {waiting ? (
        <ConnectWaiting />
      ) : unlock ? (
        <PressRow
          glyph={<AskMark />}
          line={unlock.line}
          note={"Connect " + namedTiles(unlock.providers) + "."}
          onPress={() => onStart(unlock.agent_id, unlock.ask, "unlock")}
        />
      ) : (
        <a href={sectionHash("connectors")} className={PRESS_ROW}>
          <IconPlug className="size-(--size-glyph) shrink-0 text-ink-soft" aria-hidden />
          <span className="min-w-0 flex-1 truncate text-ink-soft">
            Connect more accounts for better suggestions.
          </span>
          <IconChevronRight className={PRESS_ROW_CHEVRON} aria-hidden />
        </a>
      )}
    </div>
  );
}

/** The connector row's place while the ranking is read: the ranking says whether this row names one
 *  app's accounts or the connectors screen, so a connect act drawn before it lands is taken away. */
export function ConnectWaiting() {
  return (
    <div className={cn(PRESS_ROW, "hover:bg-transparent")} aria-hidden>
      <Skeleton className="size-(--size-glyph) shrink-0" />
      <Skeleton className="h-(--size-glyph) w-3/5" />
    </div>
  );
}
