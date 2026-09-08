import { IconCheck, IconCopy, IconMessage, IconTerminal2 } from "@tabler/icons-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { SILENT, Toast, type ToastState } from "@/components/ui/toast";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { Notice, Panel, PanelSkeleton, usePanelRead } from "@/kernel/panel";
import { postObjectAction, type ObjectAction } from "@/lib/api";
import { useViewer } from "@/lib/audience";
import { BrandMark } from "@/lib/brandMark";
import { cn } from "@/lib/cn";
import { ConsentLink, openConsentWindow } from "@/lib/consent";
import { useMainAgent } from "@/lib/mainAgent";
import type { ActionView, Agent, Member } from "@/lib/types";
import { FIRST_RUN_READ, WATCH_MS, type FirstRunPayload } from "@/views/FirstRun";

export const SURFACES_READ = "/workspace/surfaces";

export type SurfaceRow = {
  name: string;
  label: string;
  offered: boolean;
  connected: boolean;
  install_command: string | null;
};

export type SurfacesPayload = { surfaces: SurfaceRow[] };

export const CONNECT_INSTALLS: Record<string, ObjectAction> = {
  slack: { kind: "surface", name: "slack", action: "slack_connect" },
};

const IMESSAGE_CONNECT: ObjectAction = {
  kind: "surface",
  name: "imessage",
  action: "imessage_connect",
};

const SLACK = "slack";
const IMESSAGE = "imessage";
const TERMINAL = "ufo";

const SUMMARIES: Record<string, string> = {
  [SLACK]: "Mention @ufo or DM it.",
  [IMESSAGE]: "Text UFO from your phone.",
  [TERMINAL]: "Chat and run tasks from your terminal.",
};

const CONNECTED = "Connected";
const POPULAR = "Popular";
const NO_IMESSAGE_PROVIDER = "This deploy has no iMessage provider.";
const NO_SLACK = "This deploy has no Slack surface.";
const NO_PUBLIC_ADDRESS = "This deploy has no public address.";
const PHONE_PLACEHOLDER = "+1 415 555 0123";
const COPIED_MS = 2_000;
const COPY_COMMAND = "Copy command";
const COPIED = "Copied";

const ROSTER_READ = "/workspace/team";

type Roster = { members: Member[]; can_add: boolean; actions: ActionView[] };

export function useConnected(name: string, held: boolean, onConnected: () => void) {
  const armed = useRef(!held);
  const state = usePanelRead<FirstRunPayload>(
    armed.current && !held ? FIRST_RUN_READ : null,
    0,
    WATCH_MS,
  );
  const landed =
    held ||
    (state.phase === "ready" &&
      state.payload.connectors.some((row) => row.name === name && row.installed));
  const reported = useRef(false);
  useEffect(() => {
    if (!armed.current || !landed || reported.current) return;
    reported.current = true;
    onConnected();
  }, [landed, onConnected]);
}

export function Connect({
  agent,
  admin,
  row,
  held,
  onConnected,
  onRefused,
  form = "step",
}: {
  agent: Agent;
  admin: boolean;
  row: { name: string; label: string };
  held: boolean;
  onConnected: () => void;
  onRefused: (message: string) => void;
  form?: "step" | "row";
}) {
  const [busy, setBusy] = useState(false);
  const [link, setLink] = useState<string | null>(null);
  useConnected(row.name, held, onConnected);

  async function connect() {
    if (busy) return;
    setBusy(true);
    // Opened on the press, before the round trip that mints the link: a window opened afterwards has lost
    // the gesture the browser opens one for.
    const consent = openConsentWindow();
    const outcome = await postObjectAction(agent.id, CONNECT_INSTALLS[row.name], {});
    setBusy(false);
    if (consent && outcome.url) consent.location.href = outcome.url;
    if (consent && !outcome.url) consent.close();
    setLink(consent ? null : (outcome.url ?? null));
    if (!outcome.url) onRefused(outcome.message);
  }

  return (
    <div
      className={cn(
        "flex flex-col gap-sm",
        form === "step" ? "w-full max-w-(--container-connect) items-center px-2xl" : "items-end",
      )}
    >
      {!admin && form === "row" ? (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <span tabIndex={0} className="inline-flex">
                <Button variant="outline" size="bar" disabled>
                  Connect
                </Button>
              </span>
            </TooltipTrigger>
            <TooltipContent side="top">{"A workspace admin connects " + row.label + "."}</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      ) : !admin ? (
        <span className="flex h-10 w-full items-center justify-center text-center text-label text-ink-soft">
          {"A workspace admin connects " + row.label + "."}
        </span>
      ) : form === "step" ? (
        <Button variant="send" size="bar" className="h-10 w-full" busy={busy} onClick={connect}>
          <BrandMark provider={row.name} onInk className="size-(--size-glyph)" />
          {"Connect " + row.label}
        </Button>
      ) : (
        <Button variant="outline" size="bar" busy={busy} onClick={connect}>
          Connect
        </Button>
      )}
      {link ? (
        <Notice>
          <ConsentLink url={link}>{"Open the " + row.label + " install page"}</ConsentLink>
        </Notice>
      ) : null}
    </div>
  );
}

const SUMMARY = "text-label leading-(--leading-chrome) text-ink-quiet";

function Connected() {
  return (
    <Badge className="gap-2xs border border-edge bg-transparent text-ink">
      <IconCheck className="size-(--size-glyph)" stroke={1.5} aria-hidden />
      {CONNECTED}
    </Badge>
  );
}

function Mark({ name }: { name: string }) {
  const glyph = "size-(--size-glyph) shrink-0 text-ink";
  switch (name) {
    case IMESSAGE:
      return <IconMessage className={glyph} stroke={1.5} aria-hidden />;
    case TERMINAL:
      return <IconTerminal2 className={glyph} stroke={1.5} aria-hidden />;
    default:
      return <BrandMark provider={name} className={glyph} />;
  }
}

function Row({
  row,
  act,
  badge,
  children,
}: {
  row: SurfaceRow;
  act: ReactNode;
  badge?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <li aria-label={row.label} className="flex gap-2xl rounded-answer border border-edge p-2xl">
      <span className="flex size-8 shrink-0 items-center justify-center rounded-avatar bg-fill">
        <Mark name={row.name} />
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-2xl">
        <div className="flex min-h-8 items-center justify-between gap-6xl">
          <div className="flex min-w-0 flex-col gap-2xs">
            <span className="flex items-center gap-sm text-body leading-(--leading-chrome) font-medium text-ink">
              {row.label}
              {badge}
            </span>
            <span className={SUMMARY}>{SUMMARIES[row.name]}</span>
          </div>
          <div className="flex shrink-0 items-center justify-end">
            {row.connected ? <Connected /> : act}
          </div>
        </div>
        {children}
      </div>
    </li>
  );
}

function Refused({ message, onRefused }: { message: string; onRefused: (message: string) => void }) {
  useEffect(() => onRefused(message), [message, onRefused]);
  return null;
}

const NOTHING = () => {};

function SlackRow({
  row,
  agent,
  member,
  onRefused,
}: {
  row: SurfaceRow;
  agent: Agent;
  member: Member;
  onRefused: (message: string) => void;
}) {
  if (!row.offered) {
    return <Row row={row} act={<span className={SUMMARY}>{NO_SLACK}</span>} />;
  }
  return (
    <Row
      row={row}
      act={
        <Connect
          agent={agent}
          admin={member.admin}
          row={row}
          held={row.connected}
          onConnected={NOTHING}
          onRefused={onRefused}
          form="row"
        />
      }
    />
  );
}

function IMessageRow({
  row,
  agent,
  onRefused,
}: {
  row: SurfaceRow;
  agent: Agent;
  onRefused: (message: string) => void;
}) {
  const [phone, setPhone] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<string | null>(null);


  async function send() {
    if (busy) return;
    setBusy(true);
    const outcome = await postObjectAction(agent.id, IMESSAGE_CONNECT, { phone_number: phone });
    setBusy(false);
    if (!outcome.applied) {
      onRefused(outcome.message);
      return;
    }
    setSent(outcome.message);
  }

  const badge = <Badge tone="affirm">{POPULAR}</Badge>;

  if (!row.offered) {
    return (
      <Row row={row} badge={badge} act={<span className={SUMMARY}>{NO_IMESSAGE_PROVIDER}</span>} />
    );
  }

  return (
    <Row
      row={row}
      badge={badge}
      act={null}
    >
      {row.connected ? null : (
        <div className="flex flex-col gap-2xl">
          <form
            className="flex items-center gap-sm"
            onSubmit={(event) => {
              event.preventDefault();
              void send();
            }}
          >
            <Input
              type="tel"
              aria-label="Phone number"
              placeholder={PHONE_PLACEHOLDER}
              value={phone}
              onChange={(event) => setPhone(event.target.value)}
              className="max-w-(--container-connect)"
            />
            <Button type="submit" size="bar" busy={busy}>
              Send code
            </Button>
          </form>
          {sent ? <span className={SUMMARY}>{sent}</span> : null}
        </div>
      )}
    </Row>
  );
}

function TerminalRow({ row }: { row: SurfaceRow }) {
  const [copied, setCopied] = useState(false);
  const fades = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (fades.current) clearTimeout(fades.current);
  }, []);

  async function copy() {
    if (row.install_command === null) return;
    await navigator.clipboard.writeText(row.install_command);
    setCopied(true);
    if (fades.current) clearTimeout(fades.current);
    fades.current = setTimeout(() => setCopied(false), COPIED_MS);
  }

  if (row.install_command === null) {
    return <Row row={row} act={<span className={SUMMARY}>{NO_PUBLIC_ADDRESS}</span>} />;
  }
  const [verb, address, pipe] = [
    row.install_command.slice(0, row.install_command.indexOf(" http")),
    row.install_command.slice(row.install_command.indexOf(" http"), row.install_command.indexOf(" |")),
    row.install_command.slice(row.install_command.indexOf(" |")),
  ];

  return (
    <Row row={row} act={null}>
      {row.connected ? null : (
        <div className="flex h-(--size-control) items-center gap-sm rounded-answer border border-edge bg-fill pr-sm pl-2xl">
          <code className="min-w-0 flex-1 truncate font-mono text-mono">
            <span className="text-attention-ink">{verb}</span>
            <span className="text-ink">{address}</span>
            <span className="text-link">{pipe}</span>
          </code>
          <Button
            variant="mark"
            size="glyph"
            aria-label={copied ? COPIED : COPY_COMMAND}
            className="text-ink-soft"
            onClick={copy}
          >
            {copied ? (
              <IconCheck stroke={1.5} aria-hidden />
            ) : (
              <IconCopy stroke={1.5} aria-hidden />
            )}
          </Button>
        </div>
      )}
    </Row>
  );
}

export function ConnectSurfaces({
  agent,
  member,
  hidden = [],
  onRefused,
}: {
  agent: Agent;
  member: Member;
  hidden?: string[];
  onRefused: (message: string) => void;
}) {
  const [settled, setSettled] = useState(false);
  const state = usePanelRead<SurfacesPayload>(SURFACES_READ, 0, settled ? undefined : WATCH_MS);
  const shown =
    state.phase === "ready"
      ? state.payload.surfaces.filter((row) => !hidden.includes(row.name))
      : [];
  const waiting = shown.some((row) => row.offered && !row.connected);
  useEffect(() => {
    if (state.phase === "ready") setSettled(!waiting);
  }, [state.phase, waiting]);

  return (
    <Panel
      state={state}
      loading={() => <PanelSkeleton shape="cards" />}
      failed={(message) => <Refused message={message} onRefused={onRefused} />}
    >
      {() => (
        <ul className="m-0 flex w-full max-w-(--container-dialog) list-none flex-col gap-sm p-0">
          {shown.map((row) => {
            switch (row.name) {
              case SLACK:
                return (
                  <SlackRow key={row.name} row={row} agent={agent} member={member} onRefused={onRefused} />
                );
              case IMESSAGE:
                return <IMessageRow key={row.name} row={row} agent={agent} onRefused={onRefused} />;
              case TERMINAL:
                return <TerminalRow key={row.name} row={row} />;
              default:
                return <Row key={row.name} row={row} act={null} />;
            }
          })}
        </ul>
      )}
    </Panel>
  );
}

export function WorkspaceMessaging() {
  const agent = useMainAgent();
  const viewer = useViewer();
  const roster = usePanelRead<Roster>(ROSTER_READ);
  const [toast, setToast] = useState<ToastState>(SILENT);
  const refuse = useCallback((title: string) => setToast({ title }), []);

  return (
    <>
      <Panel
        state={roster}
        loading={() => <PanelSkeleton shape="cards" />}
        failed={(message) => <Refused message={message} onRefused={refuse} />}
      >
        {(payload) => {
          const member = payload.members.find((row) => row.email === viewer);
          if (!agent || !member) return null;
          return <ConnectSurfaces agent={agent} member={member} onRefused={refuse} />;
        }}
      </Panel>
      <Toast state={toast} onDone={() => setToast(SILENT)} />
    </>
  );
}

