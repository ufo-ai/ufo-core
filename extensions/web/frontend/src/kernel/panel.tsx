import { useEffect, useState, type ReactNode } from "react";

import { getJson } from "@/lib/api";
import { cn } from "@/lib/cn";

export type PanelState<T> =
  | { phase: "loading" }
  | { phase: "failed"; message: string; status: number }
  | { phase: "ready"; payload: T };

export function usePanelRead<T>(path: string | null, reloads: number = 0): PanelState<T> {
  const [state, setState] = useState<PanelState<T>>({ phase: "loading" });
  useEffect(() => {
    if (path === null) return;
    const superseded = new AbortController();
    let live = true;
    setState({ phase: "loading" });
    getJson<T>(path, superseded.signal).then((result) => {
      if (!live) return;
      setState(
        result.ok
          ? { phase: "ready", payload: result.payload }
          : { phase: "failed", message: result.message, status: result.status },
      );
    });
    return () => {
      live = false;
      superseded.abort();
    };
  }, [path, reloads]);
  return state;
}

export function Panel<T>({
  state,
  loading,
  failed,
  empty,
  children,
}: {
  state: PanelState<T>;
  loading?: () => ReactNode;
  failed?: (message: string, status: number) => ReactNode;
  empty?: (payload: T) => ReactNode;
  children: (payload: T) => ReactNode;
}) {
  if (state.phase === "loading")
    return loading ? loading() : <PanelEmpty>Loading…</PanelEmpty>;
  if (state.phase === "failed")
    return failed ? failed(state.message, state.status) : <PanelEmpty>{state.message}</PanelEmpty>;
  const nothing = empty?.(state.payload);
  if (nothing) return <PanelEmpty>{nothing}</PanelEmpty>;
  return children(state.payload);
}

function Empty({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={cn("m-auto max-w-empty text-center opacity-(--muted-soft)", className)}>
      {children}
    </div>
  );
}

export function PanelEmpty({ children }: { children: ReactNode }) {
  return <Empty className="my-6xl mx-auto block">{children}</Empty>;
}

export type NoticeState = { text: string; refused: boolean };

export const QUIET: NoticeState = { text: "", refused: false };

export function outcomeNotice(outcome: { applied: boolean; message: string }): NoticeState {
  return { text: outcome.message, refused: !outcome.applied };
}

export function OutcomeNotice({ state }: { state: NoticeState }) {
  return <Notice tone={state.refused ? "attention" : "quiet"}>{state.text}</Notice>;
}

export function Notice({
  tone = "quiet",
  children,
}: {
  tone?: "quiet" | "attention";
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "mt-md min-h-[1em] font-mono text-mono",
        tone === "attention" && children
          ? "w-fit rounded-sm bg-attention px-sm py-hair [color:var(--color-attention-ink)]"
          : null,
      )}
    >
      {children}
    </div>
  );
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mb-5xl max-w-section">
      <h2 className="text-label m-0 mb-xs opacity-(--muted-soft)">{title}</h2>
      {children}
    </section>
  );
}
