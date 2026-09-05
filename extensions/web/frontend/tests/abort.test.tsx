import { render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { expect, onTestFinished, test, vi } from "vitest";

import type { Fetched } from "@/lib/api";

/** A read the test settles by hand, so a case can reject one the way a browser rejects a fetch the
 *  surface aborted — which the real `getJson` never does, because it answers every fault as a
 *  `Fetched` row. */
type Read = {
  signal?: AbortSignal;
  answer: (payload: unknown) => void;
  fail: (reason: unknown) => void;
};

const reads: Read[] = [];

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    getJson: (_path: string, signal?: AbortSignal) =>
      new Promise((resolve, reject) => {
        reads.push({
          signal,
          answer: (payload) => resolve({ ok: true, payload } as Fetched<unknown>),
          fail: reject,
        });
      }),
  };
});

const { Panel, usePanelRead } = await import("@/kernel/panel");
const { ApplicationAction } = await import("@/apps/action");

const ABORT_MESSAGE = "signal is aborted without reason";

function abortError(): DOMException {
  return new DOMException(ABORT_MESSAGE, "AbortError");
}

/** Every rejection the run left for the browser to report. A read rejected by our own cleanup must
 *  leave none: the member's screen keeps drawing, and the monitor watching unhandled errors in prod
 *  stays quiet. */
function unhandledRejections(): unknown[] {
  const seen: unknown[] = [];
  const note = (reason: unknown) => void seen.push(reason);
  process.on("unhandledRejection", note);
  onTestFinished(() => void process.off("unhandledRejection", note));
  return seen;
}

/** Two macrotask turns: node reports an unhandled rejection after the microtask queue drains. */
async function settled(): Promise<void> {
  for (let turn = 0; turn < 2; turn += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}

function Pane({ path }: { path: string }) {
  const state = usePanelRead<{ name: string }>(path);
  return <Panel state={state}>{(payload) => <span>{payload.name}</span>}</Panel>;
}

function Switcher() {
  const [path, setPath] = useState("/first");
  return (
    <>
      <button type="button" onClick={() => setPath("/second")}>
        go
      </button>
      <Pane path={path} />
    </>
  );
}

const ACTION = {
  label: "Assign issue 521 to Alex",
  write: {
    function: "ufoWrite" as const,
    arguments: ["eval_app_action", "assign-issue-521", { value: "alex" }] as [
      string,
      string,
      Record<string, unknown>,
    ],
  },
  read: {
    function: "ufoRead" as const,
    arguments: ["objects/eval_app_action/assign-issue-521"] as [string],
  },
};

test("a panel read the member superseded raises nothing when its abort rejects", async () => {
  const seen = unhandledRejections();
  reads.length = 0;
  const { unmount } = render(<Switcher />);
  await waitFor(() => expect(reads).toHaveLength(1));

  unmount();
  expect(reads[0].signal?.aborted).toBe(true);
  reads[0].fail(abortError());
  await settled();

  expect(seen).toEqual([]);
});

test("a panel read that breaks for anything but an abort states the failure", async () => {
  const seen = unhandledRejections();
  reads.length = 0;
  render(<Pane path="/first" />);
  await waitFor(() => expect(reads).toHaveLength(1));

  reads[0].fail(new TypeError("Failed to fetch"));

  expect(await screen.findByText("Network error — try again.")).toBeTruthy();
  await settled();
  expect(seen).toEqual([]);
});

test("an action row's read raises nothing when the row goes away and its abort rejects", async () => {
  const seen = unhandledRejections();
  reads.length = 0;
  const { unmount } = render(<ApplicationAction action={ACTION} />);
  await waitFor(() => expect(reads).toHaveLength(1));

  unmount();
  expect(reads[0].signal?.aborted).toBe(true);
  reads[0].fail(abortError());
  await settled();

  expect(seen).toEqual([]);
});

test("an action row states a read that broke for anything but an abort", async () => {
  const seen = unhandledRejections();
  reads.length = 0;
  render(<ApplicationAction action={ACTION} />);
  await waitFor(() => expect(reads).toHaveLength(1));

  reads[0].fail(new TypeError("Failed to fetch"));

  expect(await screen.findByText("Network error — try again.")).toBeTruthy();
  await settled();
  expect(seen).toEqual([]);
});
