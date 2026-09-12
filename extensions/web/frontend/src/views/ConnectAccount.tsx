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

import { ConnectedBadge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemSeparator,
  ItemTitle,
  MarkTile,
} from "@/components/ui/item";
import { Input } from "@/components/ui/field";
import { Sheet } from "@/components/ui/sheet";
import { BASE } from "@/lib/api";
import { BrandMark } from "@/lib/brandMark";
import { cn } from "@/lib/cn";

const ACCOUNTS_READ = "/workspace/accounts";
const SETTINGS_URL = "https://chatgpt.com/#settings/Security";
const SIGN_IN_REFUSAL = "Your session ended. Sign in again to connect an account.";
const NETWORK_REFUSAL = "Could not reach the workspace. Try again.";
const AVAILABLE_IN = "Available in the coding subagent.";
/** Every row this list draws is a coding provider, and the coding subagent is what runs on one, so
 *  the chip stands on the row whether the account is connected yet or not. */

type Account = { provider: string; label: string; connected: boolean };
type Grant = { user_code: string; verification_uri: string; interval: number } | { error: string };
type Claim = { status: "pending" | "connected" | "refused"; message?: string };

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
  /** A proxy answering an error page still answers 200, and a rejection escaping here reaches nothing
   *  that could handle it. */
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
      className={cn(buttonVariants({ variant: "outline", size: "bar" }), "gap-sm")}
    >
      {children}
      <IconExternalLink className="size-icon" aria-hidden />
    </a>
  );
}

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
  /** Armed by the code and nothing else: held on the interval's own dependencies it would be rebuilt on
   *  every render the mount above does, and an interval that never survives to its own tick never fires. */
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
    <div className="flex flex-col gap-2xl">
      <ol className="m-0 grid list-none gap-2xl p-0">
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
            <code className="rounded-input bg-raised px-2xl py-sm font-mono text-title tracking-code">
              {code.user_code}
            </code>
          ) : null}
        </Step>
      </ol>
      {refusal ? <p className="m-0 text-label text-danger">{refusal}</p> : null}
    </div>
  );
}

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
    <div className="flex flex-col gap-2xl">
      <ol className="m-0 grid list-none gap-2xl p-0">
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

  const settled = useCallback(() => setLanded(true), []);

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
                  <ItemTitle>
                    <span className="flex items-center gap-sm">
                      {account.label}
                      {account.connected ? <ConnectedBadge label={account.label} /> : null}
                    </span>
                  </ItemTitle>
                  <ItemDescription>{AVAILABLE_IN}</ItemDescription>
                </ItemContent>
                <ItemActions>
                  <Button
                    variant={account.connected ? "row" : "outline"}
                    size="bar"
                    onClick={() => {
                      setLanded(false);
                      setAsking(account);
                    }}
                  >
                    {account.connected ? "Configure" : "Connect"}
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
      {asking ? (
        <Sheet
          open
          title={`Connect ${asking.label}`}
          onClose={close}
        >
          {landed ? (
            <p className="m-0">{`${asking.label} connected.`}</p>
          ) : (
            <>
              {asking.provider === "openai" ? <Chatgpt onConnected={settled} /> : null}
              {asking.provider === "anthropic" ? <Claude onConnected={settled} /> : null}
            </>
          )}
        </Sheet>
      ) : null}
    </div>
  );
}
