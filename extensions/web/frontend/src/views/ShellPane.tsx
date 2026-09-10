import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import { useEffect, useRef, useState } from "react";

import { BASE, shellPath } from "@/lib/api";
import type { ConversationAgent } from "@/lib/types";

export type ShellReport = { available: boolean; active: boolean; cwd: string };

/** How often a visible terminal states its grid again. The lease renews only for a terminal heard
 *  from, so a tab behind another window keeps its shell and stops holding the container awake. */
const BEAT_MS = 30_000;
const TERMINAL_FONT_SIZE = 12;

function shellSocketUrl(agentId: string, conversationId: string): string {
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  return scheme + "//" + window.location.host + BASE + shellPath(agentId, conversationId);
}

function terminalTheme(host: HTMLElement): { background: string; foreground: string } {
  const drawn = window.getComputedStyle(host);
  return { background: drawn.backgroundColor, foreground: drawn.color };
}

/** The conversation's sandbox as a terminal. The socket is the whole mechanism: opening it resumes
 *  the sandbox and starts a shell in the workspace, output arrives as bytes the emulator writes, and
 *  keystrokes go back as bytes. Every text frame states the grid — the first one the size the shell
 *  is born at, each later one a resize or the beat that says the tab is still being watched. */
export function ShellPane({
  agent,
  conversationId,
  cwd,
}: {
  agent: ConversationAgent;
  conversationId: string;
  cwd: string;
}) {
  const host = useRef<HTMLDivElement | null>(null);
  const [connected, setConnected] = useState(false);
  const [ended, setEnded] = useState<string | null>(null);
  useEffect(() => {
    const element = host.current;
    if (!element) return;
    const terminal = new Terminal({
      cursorBlink: true,
      fontFamily: window.getComputedStyle(element).fontFamily,
      fontSize: TERMINAL_FONT_SIZE,
      theme: terminalTheme(element),
    });
    const fit = new FitAddon();
    terminal.loadAddon(fit);
    terminal.open(element);
    fit.fit();
    const socket = new WebSocket(shellSocketUrl(agent.id, conversationId));
    socket.binaryType = "arraybuffer";
    const grid = () => {
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ cols: terminal.cols, rows: terminal.rows }));
      }
    };
    socket.onopen = () => {
      setConnected(true);
      grid();
      terminal.focus();
    };
    socket.onmessage = (event) => terminal.write(new Uint8Array(event.data as ArrayBuffer));
    socket.onclose = (event) => {
      setConnected(false);
      setEnded(event.reason || "The shell ended.");
    };
    const typed = terminal.onData((data) => {
      if (socket.readyState === WebSocket.OPEN) socket.send(new TextEncoder().encode(data));
    });
    const resized = terminal.onResize(grid);
    const observer = new ResizeObserver(() => fit.fit());
    observer.observe(element);
    const beat = window.setInterval(() => {
      if (document.visibilityState === "visible") grid();
    }, BEAT_MS);
    return () => {
      window.clearInterval(beat);
      observer.disconnect();
      typed.dispose();
      resized.dispose();
      socket.close();
      terminal.dispose();
    };
  }, [agent.id, conversationId]);
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-sm" data-testid="shell">
      <p className="text-small text-ink-soft">
        {ended ?? (connected ? cwd : "Starting the sandbox…")}
      </p>
      <div
        ref={host}
        data-slot="terminal"
        className="min-h-0 flex-1 overflow-hidden rounded-panel bg-fill p-sm font-mono text-ink"
      />
    </div>
  );
}
