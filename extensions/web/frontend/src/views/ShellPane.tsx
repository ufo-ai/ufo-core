import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import { useEffect, useRef } from "react";

import { BASE, shellPath } from "@/lib/api";
import type { ConversationAgent } from "@/lib/types";

export type ShellReport = { available: boolean; active: boolean };

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
 *  is born at, each later one a resize or the beat that says the tab is still being watched.
 *
 *  The terminal is the whole surface: what the member reads about their own shell is what the shell
 *  printed, so a connection that gets no sandbox states why here, and a shell that exits reports
 *  through `onEnded` rather than leaving a dead terminal on the screen. */
export function ShellPane({
  agent,
  conversationId,
  onEnded,
}: {
  agent: ConversationAgent;
  conversationId: string;
  onEnded: () => void;
}) {
  const host = useRef<HTMLDivElement | null>(null);
  const ended = useRef(onEnded);
  ended.current = onEnded;
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
    terminal.write("Starting the sandbox…\r\n");
    const socket = new WebSocket(shellSocketUrl(agent.id, conversationId));
    socket.binaryType = "arraybuffer";
    const grid = () => {
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ cols: terminal.cols, rows: terminal.rows }));
      }
    };
    socket.onopen = () => {
      grid();
      terminal.focus();
    };
    socket.onmessage = (event) => terminal.write(new Uint8Array(event.data as ArrayBuffer));
    socket.onclose = (event) => {
      if (event.reason) terminal.write("\r\n" + event.reason + "\r\n");
      else ended.current();
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
      socket.onclose = null;
      socket.close();
      terminal.dispose();
    };
  }, [agent.id, conversationId]);
  return (
    <div
      ref={host}
      data-testid="shell"
      data-slot="terminal"
      data-keeps-escape
      className="min-h-0 flex-1 overflow-hidden rounded-panel bg-fill p-sm font-mono text-ink"
    />
  );
}
