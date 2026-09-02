import { IconCheck, IconExternalLink } from "@tabler/icons-react";
import {
  Fragment,
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemGroup,
  ItemSeparator,
  ItemTitle,
  MarkTile,
} from "@/components/ui/item";
import { Input } from "@/components/ui/field";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { BASE } from "@/lib/api";
import { BrandMark } from "@/lib/brandMark";
import { cn } from "@/lib/cn";

const ACCOUNTS_READ = "/workspace/accounts";
const SETTINGS_URL = "https://chatgpt.com/#settings/Security";
const SIGN_IN_REFUSAL = "Your session ended. Sign in again to connect an account.";
const NETWORK_REFUSAL = "Could not reach the workspace. Try again.";

type Account = { provider: string; label: string; connected: boolean };
type Grant = { user_code: string; verification_uri: string; interval: number } | { error: string };
type Claim = { status: "pending" | "connected" | "refused"; message?: string };

/** Every answer these routes give, told apart from a session that ended and from a workspace that
 *  could not be reached — a member who is signed out must read that, not "try again". */
async function ask<T>(path: string, fields?: Record<string, string>): Promise<T | string> {
  const res = await fetch(BASE + path, {
    method: fields ? "POST" : "GET",
    credentials: "same-origin",
    ...(fields
      ? {
          headers: { "content-type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams(fields).toString(),
        }
      : {}),
  }).catch(() => null);
  if (!res) return NETWORK_REFUSAL;
  if (res.status === 401) return SIGN_IN_REFUSAL;
  if (!res.ok) return NETWORK_REFUSAL;
  /** A body that will not parse is read the same as a workspace that could not be reached: a proxy
   *  answering an error page still answers 200, and a rejection escaping here reaches nothing that
   *  could handle it. */
  const answered = await res.json().catch(() => null);
  return answered === null ? NETWORK_REFUSAL : (answered as T);
}

function Step({ index, children }: { index: number; children: ReactNode }) {
  return (
    <li className="grid grid-cols-[1.75rem_1fr] items-start gap-sm">
      <b className="grid size-7 place-content-center rounded-full border border-edge font-normal text-ink-soft">
        {index}
      </b>
      <div className="flex flex-col items-start gap-sm">{children}</div>
    </li>
  );
}

function Away({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener"
      className={cn(buttonVariants({ variant: "outline" }), "gap-xs")}
    >
      {children}
      <IconExternalLink className="size-icon" aria-hidden />
    </a>
  );
}

/** The ChatGPT asking: the grant OpenAI opens, the code the member types on its own page, and the
 *  polling that carries them on the moment they approve. Step one is the account setting the grant
 *  needs — it is off by default, and it is the most common reason the ask is refused. */
function Chatgpt({ onConnected }: { onConnected: () => void }) {
  const [grant, setGrant] = useState<Grant | null>(null);
  const [refusal, setRefusal] = useState("");
  const [busy, setBusy] = useState(true);

  useEffect(() => {
    let live = true;
    void (async () => {
      const opened = await ask<Grant>("/openai/device", {});
      if (!live) return;
      setBusy(false);
      if (typeof opened === "string") return setRefusal(opened);
      if ("error" in opened) return setRefusal(opened.error);
      setGrant(opened);
    })();
    return () => {
      live = false;
    };
  }, []);

  const code = grant && !("error" in grant) ? grant : null;
  /** The poll is armed by the code and by nothing else. Held on the interval's own dependencies it
   *  would be torn down and rebuilt on every render the mount above happens to do, and an interval
   *  that never survives to its own tick never fires at all. */
  const settle = useRef(onConnected);
  settle.current = onConnected;
  useEffect(() => {
    if (!code) return;
    let live = true;
    const poll = async () => {
      const claim = await ask<Claim>("/openai/device/poll");
      if (!live) return;
      if (typeof claim === "string") return setRefusal(claim);
      if (claim.status === "connected") return settle.current();
      if (claim.status === "refused") {
        setGrant(null);
        setRefusal(claim.message ?? "");
      }
    };
    const watch = setInterval(poll, Math.max(code.interval, 1) * 1000);
    return () => {
      live = false;
      clearInterval(watch);
    };
  }, [code]);

  return (
    <div className="flex flex-col gap-lg">
      <ol className="m-0 grid list-none gap-lg p-0">
        <Step index={1}>
          <p className="m-0">Turn on device code authorization for your account.</p>
          <Away href={SETTINGS_URL}>ChatGPT settings</Away>
        </Step>
        <Step index={2}>
          <p className="m-0">Open the device page and enter the code.</p>
          {code ? <Away href={code.verification_uri}>Open ChatGPT</Away> : null}
        </Step>
        <Step index={3}>
          <p className="m-0">Enter this code when ChatGPT asks for it.</p>
          {busy ? <p className="m-0 text-ink-soft">Asking ChatGPT…</p> : null}
          {code ? (
            <code className="rounded-input bg-raised px-md py-sm font-mono text-title tracking-code">
              {code.user_code}
            </code>
          ) : null}
        </Step>
      </ol>
      {refusal ? <p className="m-0 text-label text-danger">{refusal}</p> : null}
    </div>
  );
}

/** The Claude asking: an authorization the member finishes at Anthropic, whose page shows them a
 *  code to carry back — nothing returns to this host, so the field is how it arrives. */
function Claude({ onConnected }: { onConnected: () => void }) {
  const [url, setUrl] = useState("");
  const [pasted, setPasted] = useState("");
  const [refusal, setRefusal] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    void (async () => {
      const opened = await ask<{ url: string }>("/anthropic/authorize", {});
      if (!live) return;
      if (typeof opened === "string") return setRefusal(opened);
      setUrl(opened.url);
    })();
    return () => {
      live = false;
    };
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setRefusal("");
    const claim = await ask<Claim>("/anthropic/code", { code: pasted.trim() });
    setBusy(false);
    if (typeof claim === "string") return setRefusal(claim);
    if (claim.status === "connected") return onConnected();
    setRefusal(claim.message ?? "");
  };

  return (
    <div className="flex flex-col gap-lg">
      <ol className="m-0 grid list-none gap-lg p-0">
        <Step index={1}>
          <p className="m-0">Open the authorization page and approve access.</p>
          {url ? <Away href={url}>Open Claude</Away> : null}
        </Step>
        <Step index={2}>
          <p className="m-0">Paste the code Claude shows you.</p>
          <form onSubmit={submit} className="flex w-full items-center gap-sm">
            <Input
              aria-label="Authorization code"
              placeholder="code#state"
              value={pasted}
              onChange={(event) => setPasted(event.target.value)}
            />
            <Button type="submit" variant="send" busy={busy} disabled={!pasted.trim()}>
              Connect
            </Button>
          </form>
        </Step>
      </ol>
      {refusal ? <p className="m-0 text-label text-danger">{refusal}</p> : null}
    </div>
  );
}

/** The coding accounts a member can connect, one act per provider, each opening that provider's
 *  asking in a dialog over whatever screen mounted this. The first run and the settings panel mount
 *  the same component and read the same rows, so neither can drift from the other about what a
 *  member holds. `onConnected` lets a mount react — the first run marks its step answered — while
 *  the acts here stay this component's own.
 *
 *  `stacked` is the first run's shape: one full-width act per provider, the height and pill of every
 *  other act in that flow, and a connected account standing as the completed signal rather than as
 *  something to manage — a member is connecting there, not maintaining. Settings draws the same
 *  accounts as a table row, where replacing and dropping one are the acts they came for. */
export function ConnectAccount({
  onConnected,
  stacked,
}: {
  onConnected?: () => void;
  stacked?: boolean;
}) {
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [asking, setAsking] = useState<Account | null>(null);
  const [landed, setLanded] = useState(false);
  const [refusal, setRefusal] = useState("");

  const reread = useCallback(async () => {
    const answer = await ask<{ accounts: Account[] }>(ACCOUNTS_READ);
    if (typeof answer === "string") return setRefusal(answer);
    setRefusal("");
    setAccounts(answer.accounts);
  }, []);

  useEffect(() => {
    void reread();
  }, [reread]);

  /** An account that landed does not take the asking down with it. The member watched a code go
   *  somewhere else and come back approved, so they are shown that it took and close the dialog
   *  themselves — a screen that vanishes on its own leaves them unsure which of the two happened. */
  const settled = useCallback(() => setLanded(true), []);

  /** Either way out of the asking. What landed is read back here rather than on the act that landed
   *  it, so a member who connected and then pressed Cancel is still holding the account. */
  const close = useCallback(async () => {
    setAsking(null);
    if (!landed) return;
    setLanded(false);
    await reread();
    onConnected?.();
  }, [landed, reread, onConnected]);

  const disconnect = async (account: Account) => {
    const answer = await ask<{ status: string }>(`/accounts/${account.provider}/disconnect`, {});
    if (typeof answer === "string") return setRefusal(answer);
    await reread();
  };

  const rows = stacked
    ? (accounts ?? []).map((account) =>
        account.connected ? (
          <span
            key={account.provider}
            className={cn(
              buttonVariants({ variant: "outline", size: "bar" }),
              "h-10 w-full text-ink-soft",
            )}
          >
            {account.label + " connected"}
            <IconCheck className="size-icon text-ink" aria-hidden />
          </span>
        ) : (
          <Button
            key={account.provider}
            variant="send"
            size="bar"
            className="h-10 w-full"
            onClick={() => {
              setLanded(false);
              setAsking(account);
            }}
          >
            {"Connect " + account.label}
          </Button>
        ),
      )
    : accounts?.length ? (
        <ItemGroup>
          {accounts.map((account, index) => (
            <Fragment key={account.provider}>
              {index ? <ItemSeparator /> : null}
              <Item>
                <MarkTile>
                  <BrandMark provider={account.provider} className="text-ink" />
                </MarkTile>
                <ItemContent>
                  <ItemTitle>{account.label}</ItemTitle>
                </ItemContent>
                <ItemActions>
                  {account.connected ? (
                    <span className="flex items-center gap-xs text-label text-ink-soft">
                      <IconCheck
                        role="img"
                        aria-label={account.label + " connected"}
                        className="size-icon"
                      />
                      Connected
                    </span>
                  ) : null}
                  <Button
                    variant={account.connected ? "row" : "outline"}
                    size="bar"
                    onClick={() => {
                      setLanded(false);
                      setAsking(account);
                    }}
                  >
                    {account.connected ? "Replace" : "Connect"}
                  </Button>
                  {account.connected ? (
                    <Button variant="row" size="bar" onClick={() => disconnect(account)}>
                      Disconnect
                    </Button>
                  ) : null}
                </ItemActions>
              </Item>
            </Fragment>
          ))}
        </ItemGroup>
      ) : null;

  return (
    <div
      className={
        stacked
          ? "flex w-full max-w-(--container-connect) flex-col gap-sm px-2xl"
          : "flex w-full flex-col gap-sm"
      }
    >
      {rows}
      {refusal ? <p className="m-0 text-label text-danger">{refusal}</p> : null}
      <Dialog open={asking !== null} onOpenChange={(shown) => (shown ? null : close())}>
        <DialogContent aria-describedby={undefined}>
          <DialogHeader>
            <DialogTitle>{asking ? `Connect ${asking.label}` : ""}</DialogTitle>
          </DialogHeader>
          {landed ? (
            <p className="m-0">{asking ? `${asking.label} connected.` : ""}</p>
          ) : (
            <>
              {asking?.provider === "openai" ? <Chatgpt onConnected={settled} /> : null}
              {asking?.provider === "anthropic" ? <Claude onConnected={settled} /> : null}
            </>
          )}
          <DialogFooter>
            <Button variant="send" className="px-3xl py-md" disabled={!landed} onClick={close}>
              OK
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
