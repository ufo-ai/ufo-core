import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState, type ComponentProps } from "react";
import { expect, onTestFinished, test, vi } from "vitest";

import { FormFromSchema, initialSpecValue, type SpecValue } from "@/kernel/form";
import { FileSheet, MediaIcon } from "@/kernel/artifact";
import { Pager } from "@/kernel/pager";
import { RowLines } from "@/kernel/rows";
import { getJson } from "@/lib/api";
import { Notice, OutcomeNotice, Panel, QUIET, Section, usePanelRead } from "@/kernel/panel";
import { CardGrid } from "@/kernel/cards";
import { DataTable } from "@/kernel/table";
import { TdActs } from "@/components/ui/table";
import { Clip, Lede, Table, Td, TdWhole, Th } from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Search } from "@/components/ui/field";
import { Sheet, SheetHost } from "@/components/ui/sheet";
import { Banded, Header, PageSearch, PageToolbar } from "@/kernel/pane";
import type { SchemaProperty } from "@/lib/types";
import { atPhoneWidth } from "./harness";

import { declaredFloor, json, opened } from "./harness";

function HostedFileSheet(props: ComponentProps<typeof FileSheet>) {
  return (
    <SheetHost>
      <FileSheet {...props} />
    </SheetHost>
  );
}

test("a sheet takes the second column of its pane and leads with its close control", async () => {
  const shut: boolean[] = [];
  render(
    <SheetHost>
      <p>Page body</p>
      <Sheet open title="Attachment" onClose={() => shut.push(true)}>
        <p>File body</p>
      </Sheet>
    </SheetHost>,
  );

  const sheet = screen.getByRole("dialog", { name: "Attachment" });
  const header = sheet.querySelector("[data-slot=sheet-header]");
  const close = within(sheet).getByRole("button", { name: "Close" });
  const columns = document.querySelectorAll("[data-slot=resizable-panel]");

  expect(columns).toHaveLength(2);
  expect(columns[0].textContent).toContain("Page body");
  expect(columns[1].contains(sheet)).toBe(true);
  expect(sheet.className).not.toContain("fixed");
  expect(document.querySelector("[data-slot=resizable-handle]")).toBeTruthy();
  expect(header?.firstElementChild).toBe(close);

  await userEvent.click(close);
  expect(shut).toEqual([true]);
});

test("the column beside a sheet keeps its own stacking context, so a lane track cannot cover it", () => {
  render(
    <SheetHost>
      <div className="absolute inset-0 z-10" data-testid="track" />
      <Sheet open title="Attachment" onClose={() => {}}>
        <p>File body</p>
      </Sheet>
    </SheetHost>,
  );

  const [body, beside] = document.querySelectorAll("[data-slot=resizable-panel]");
  const holder = screen.getByTestId("track").parentElement!;
  expect(holder.className).toContain("relative");
  expect(holder.className).toContain("isolate");
  expect(holder.closest("[data-slot=resizable-panel]")).toBe(body);
  expect(beside.compareDocumentPosition(body) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
});

test("a sheet covers its pane at a phone's width, where there is no room to split", async () => {
  atPhoneWidth();
  render(
    <SheetHost>
      <p>Page body</p>
      <Sheet open title="Attachment" onClose={() => {}}>
        <p>File body</p>
      </Sheet>
    </SheetHost>,
  );

  const sheet = await screen.findByRole("dialog", { name: "Attachment" });
  expect(document.querySelectorAll("[data-slot=resizable-panel]")).toHaveLength(1);
  expect(document.querySelector("[data-slot=resizable-handle]")).toBeNull();
  expect(sheet.closest("[data-slot=resizable-panel]")).toBeNull();
  expect(screen.getByText("File body")).toBeTruthy();
});

test("a pane holding no open sheet keeps its whole width and offers no handle", () => {
  render(
    <SheetHost>
      <p>Page body</p>
      <Sheet open={false} title="Attachment" onClose={() => {}}>
        <p>File body</p>
      </Sheet>
    </SheetHost>,
  );

  expect(document.querySelectorAll("[data-slot=resizable-panel]")).toHaveLength(1);
  expect(document.querySelector("[data-slot=resizable-handle]")).toBeNull();
  expect(screen.queryByText("File body")).toBeNull();
});

test("a file sheet owns the shared title, metadata, preview, and download", () => {
  render(
    <HostedFileSheet
      file={{
        filename: "report.png",
        subject: "Quarterly report",
        media_type: "image/png",
        size_bytes: 2048,
        url: "/files/report.png",
        preview_url: "/previews/report.png",
      }}
      onClose={() => {}}
      details={<div>Shared by Mel</div>}
    />,
  );

  const sheet = screen.getByRole("dialog", { name: "report.png" });
  expect(within(sheet).getByText("Quarterly report · image/png · 2 kB")).toBeTruthy();
  expect(within(sheet).getByText("Shared by Mel")).toBeTruthy();
  expect(within(sheet).getByRole("img", { name: "Quarterly report" }).getAttribute("src")).toBe(
    "/previews/report.png",
  );
  const download = within(sheet).getByRole("link", { name: "Download" });
  expect(download.getAttribute("href")).toBe("/files/report.png");
  const header = sheet.querySelector('[data-slot="sheet-header"]');
  expect(header).toBeTruthy();
  expect(header!.contains(download)).toBe(false);
});

test("a file sheet stacks a document's pages and renders the next batch on request", async () => {
  const asked: string[] = [];
  const page = (at: number) => "cGFnZS0" + String(at);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (!String(url).includes("/preview")) return new Response("%PDF-1.7");
      const from = Number(String((init?.body as FormData).get("start_page")));
      asked.push(String(from));
      const drawn = from === 1 ? 8 : 4;
      return Response.json({
        start_page: from,
        page_count: 12,
        pages: Array.from({ length: drawn }, (_at, index) => page(from + index)),
      });
    }),
  );

  render(
    <HostedFileSheet
      file={{
        filename: "report.pdf",
        subject: "Quarterly report",
        media_type: "application/pdf",
        size_bytes: 4096,
        url: "/files/report.pdf",
        preview_url: "/previews/report.png",
      }}
      onClose={() => {}}
    />,
  );

  const sheet = screen.getByRole("dialog", { name: "report.pdf" });
  const pages = () => within(sheet).queryAllByRole("img");
  await waitFor(() => expect(pages().length).toBe(8));
  expect(pages()[0].getAttribute("src")).toBe("data:image/png;base64," + page(1));
  expect(pages()[0].getAttribute("alt")).toBe("report.pdf");
  expect(pages()[7].getAttribute("alt")).toBe("report.pdf page 8");
  const stack = pages()[0].parentElement as HTMLElement;
  expect(stack.getAttribute("data-slot")).toBe("file-pages");
  expect(stack.closest("[data-slot=sheet-content]")).toBe(sheet);

  const more = within(sheet).getByRole("button", { name: "Load more pages of report.pdf" });
  expect(more.textContent).toBe("+4 more");
  expect(more.previousElementSibling).toBe(pages()[7]);

  await userEvent.click(more);
  await waitFor(() => expect(pages().length).toBe(12));
  expect(pages()[11].getAttribute("src")).toBe("data:image/png;base64," + page(12));
  expect(asked).toEqual(["1", "9"]);
  expect(
    within(sheet).queryByRole("button", { name: "Load more pages of report.pdf" }),
  ).toBeNull();
});

test("a file sheet draws a one-page document as the picture the store rendered", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      String(url).includes("/preview")
        ? Response.json({ start_page: 1, page_count: 1, pages: ["cGFnZS0x"] })
        : new Response("%PDF-1.7"),
    ),
  );

  render(
    <HostedFileSheet
      file={{
        filename: "note.pdf",
        subject: "One pager",
        media_type: "application/pdf",
        size_bytes: 1024,
        url: "/files/note.pdf",
        preview_url: "/previews/note.png",
      }}
      onClose={() => {}}
    />,
  );

  const sheet = screen.getByRole("dialog", { name: "note.pdf" });
  const drawn = within(sheet).getByRole("img", { name: "One pager" });
  expect(drawn.getAttribute("src")).toBe("/previews/note.png");
  await waitFor(() => expect(within(sheet).getAllByRole("img").length).toBe(1));
  expect(within(sheet).getByRole("img", { name: "One pager" }).getAttribute("src")).toBe(
    "/previews/note.png",
  );
  expect(sheet.querySelector("[data-slot=file-pages]")).toBeNull();
  expect(within(sheet).queryByRole("button", { name: /Load more pages/ })).toBeNull();
});

test("a file sheet drops the batch of a file the member has already left", async () => {
  let land: () => void = () => {};
  const held = new Promise<void>((settle) => {
    land = settle;
  });
  const page = (name: string, at: number) => "cGFnZS0" + name + String(at);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (!String(url).includes("/preview")) return new Response("%PDF-1.7");
      const form = init?.body as FormData;
      const name = (form.get("file") as File).name;
      const from = Number(String(form.get("start_page")));
      if (name === "report.pdf" && from > 1) await held;
      const count = name === "report.pdf" ? 12 : 3;
      const drawn = from === 1 ? Math.min(8, count) : 4;
      return Response.json({
        start_page: from,
        page_count: count,
        pages: Array.from({ length: drawn }, (_at, index) => page(name, from + index)),
      });
    }),
  );

  const report = {
    filename: "report.pdf",
    subject: null,
    media_type: "application/pdf",
    size_bytes: 4096,
    url: "/files/report.pdf",
    preview_url: "/previews/report.png",
  };
  const notes = {
    ...report,
    filename: "notes.pdf",
    url: "/files/notes.pdf",
    preview_url: "/previews/notes.png",
  };

  const { rerender } = render(<HostedFileSheet file={report} onClose={() => {}} />);
  const pages = () => within(screen.getByRole("dialog")).queryAllByRole("img");
  await waitFor(() => expect(pages().length).toBe(8));
  await userEvent.click(screen.getByRole("button", { name: "Load more pages of report.pdf" }));

  rerender(<HostedFileSheet file={notes} onClose={() => {}} />);
  await waitFor(() => expect(pages().length).toBe(3));
  await act(async () => {
    land();
    await new Promise((settled) => setTimeout(settled, 0));
  });

  expect(screen.getByRole("dialog", { name: "notes.pdf" })).toBeTruthy();
  expect(pages().map((drawn) => drawn.getAttribute("src"))).toEqual(
    [1, 2, 3].map((at) => "data:image/png;base64," + page("notes.pdf", at)),
  );
  expect(screen.queryByRole("button", { name: /Load more pages/ })).toBeNull();
});

test("a file sheet draws a video's cover without rendering the file again", async () => {
  const asked = vi.fn(async () => new Response("bytes"));
  vi.stubGlobal("fetch", asked);

  render(
    <HostedFileSheet
      file={{
        filename: "demo.mp4",
        subject: "Product demo",
        media_type: "video/mp4",
        size_bytes: 40 * 1024 * 1024,
        url: "/files/demo.mp4",
        preview_url: "/previews/demo.png",
      }}
      onClose={() => {}}
    />,
  );

  const sheet = screen.getByRole("dialog", { name: "demo.mp4" });
  await act(async () => {});
  expect(asked).not.toHaveBeenCalled();
  expect(within(sheet).getByRole("img", { name: "Product demo" }).getAttribute("src")).toBe(
    "/previews/demo.png",
  );
  expect(sheet.querySelector("[data-slot=file-pages]")).toBeNull();
});

const OFFICE_DOCUMENTS = [
  ["brief.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
  ["deck.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"],
  ["ledger.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"],
] as const;

test("a file sheet stacks the pages of every office document the route renders", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      String(url).includes("/preview")
        ? Response.json({ start_page: 1, page_count: 2, pages: ["cGFnZS0x", "cGFnZS0y"] })
        : new Response("PK"),
    ),
  );

  for (const [filename, mediaType] of OFFICE_DOCUMENTS) {
    render(
      <HostedFileSheet
        file={{
          filename,
          subject: null,
          media_type: mediaType,
          size_bytes: 8192,
          url: "/files/" + filename,
          preview_url: "/previews/" + filename + ".png",
        }}
        onClose={() => {}}
      />,
    );
    const sheet = screen.getByRole("dialog", { name: filename });
    await waitFor(() => expect(within(sheet).getAllByRole("img").length).toBe(2));
    expect(within(sheet).getAllByRole("img")[1].getAttribute("alt")).toBe(filename + " page 2");
    cleanup();
  }
});

test("a file sheet never draws raw SVG bytes", () => {
  render(
    <HostedFileSheet
      file={{
        filename: "design.svg",
        subject: "Application wireframe",
        media_type: "image/svg+xml",
        size_bytes: 2048,
        url: "/files/design.svg",
        preview_url: null,
      }}
      onClose={() => {}}
    />,
  );

  const sheet = screen.getByRole("dialog", { name: "design.svg" });
  expect(within(sheet).queryByRole("img")).toBeNull();
  expect(within(sheet).getByText("No preview for this file type. Download it to open it.")).toBeTruthy();
  expect(within(sheet).getByRole("link", { name: "Download" }).getAttribute("href")).toBe(
    "/files/design.svg",
  );
});

test("a file sheet renders a markdown document instead of its image preview", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("# Findings\n\nThe number moved.")));

  render(
    <HostedFileSheet
      file={{
        filename: "report.md",
        subject: "Quarterly report",
        media_type: "text/markdown",
        size_bytes: 32,
        url: "/files/report.md",
        preview_url: "/previews/report.png",
      }}
      onClose={() => {}}
    />,
  );

  const sheet = screen.getByRole("dialog", { name: "report.md" });
  const heading = await within(sheet).findByRole("heading", { name: "Findings" });
  expect(within(sheet).getByText("The number moved.")).toBeTruthy();
  expect(heading.closest("[data-artifact-document]")?.className).not.toContain("max-h-24");
  expect(within(sheet).queryByRole("img")).toBeNull();
});

test("a file sheet renders a code file's characters instead of stating no preview", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("services:\n  web: {}")));

  render(
    <HostedFileSheet
      file={{
        filename: "compose.yaml",
        subject: null,
        media_type: "application/yaml",
        size_bytes: 24,
        url: "/files/compose.yaml",
        preview_url: null,
      }}
      onClose={() => {}}
    />,
  );

  const sheet = screen.getByRole("dialog", { name: "compose.yaml" });
  expect(await within(sheet).findByText(/services:/)).toBeTruthy();
  expect(
    within(sheet).queryByText("No preview for this file type. Download it to open it."),
  ).toBeNull();
});

test("a code file draws the document glyph and a file of unknown bytes the plain sheet", () => {
  const { container } = render(
    <>
      <MediaIcon mediaType="application/yaml" />
      <MediaIcon mediaType="application/typescript" />
      <MediaIcon mediaType="application/octet-stream" />
    </>,
  );
  expect(container.querySelectorAll("svg.tabler-icon-file-text")).toHaveLength(2);
  expect(container.querySelectorAll("svg.tabler-icon-file")).toHaveLength(1);
});

type Row = { name: string };

function Listing({ path }: { path: string }) {
  const state = usePanelRead<{ rows: Row[] }>(path);
  return (
    <Panel state={state} empty={(payload) => (payload.rows.length ? null : "Nothing listed.")}>
      {(payload) => (
        <DataTable
          columns={["name"]}
          rows={payload.rows}
          rowKey={(row) => row.name}
          empty="unreachable"
        >
          {(row) => <Td>{row.name}</Td>}
        </DataTable>
      )}
    </Panel>
  );
}

/** The phone rule keeps a row's acts by this mark, so the column the table draws itself and the one
 *  a caller draws both have to carry it or an act goes off the side of a phone. */
test("the acts a row carries name themselves, whichever column draws them", () => {
  render(
    <DataTable
      columns={["name", { label: "", fact: true }]}
      rows={[{ name: "one" }]}
      rowKey={(row) => row.name}
      empty="unreachable"
      act={() => "Open"}
    >
      {(row) => (
        <>
          <Td>{row.name}</Td>
          <TdActs>
            <button type="button">Menu</button>
          </TdActs>
        </>
      )}
    </DataTable>,
  );

  const marked = [...window.document.querySelectorAll("[data-acts]")];
  expect(marked.map((cell) => cell.tagName)).toEqual(["TH", "TD", "TD"]);
  const row = screen.getByText("one").closest("tr") as HTMLTableRowElement;
  expect([...row.cells].filter((cell) => cell.hasAttribute("data-acts")).length).toBe(2);
});

test("the fence discards a read the member superseded while the view stayed mounted", async () => {
  const pending = new Map<string, (value: Response) => void>();
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (url: string) =>
        new Promise<Response>((resolve) => {
          pending.set(url, resolve);
        }),
    ),
  );

  function Switcher() {
    const [page, setPage] = useState("/first");
    return (
      <>
        <button type="button" onClick={() => setPage("/second")}>
          go
        </button>
        <Listing path={page} />
      </>
    );
  }

  render(<Switcher />);
  await waitFor(() => expect(pending.has("/surface/web/first")).toBe(true));

  await userEvent.click(screen.getByRole("button", { name: "go" }));
  await waitFor(() => expect(pending.has("/surface/web/second")).toBe(true));

  pending.get("/surface/web/second")!(json({ rows: [{ name: "chosen" }] }));
  expect(await screen.findByText("chosen")).toBeTruthy();

  pending.get("/surface/web/first")!(json({ rows: [{ name: "stale" }] }));
  await new Promise((resolve) => setTimeout(resolve, 0));

  expect(screen.queryByText("stale")).toBeNull();
  expect(screen.getByText("chosen")).toBeTruthy();
});

test("the fence aborts the request the member left behind", async () => {
  const signals: AbortSignal[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (_url: string, init?: RequestInit) =>
        new Promise<Response>(() => {
          if (init?.signal) signals.push(init.signal);
        }),
    ),
  );

  function Switcher() {
    const [page, setPage] = useState("/first");
    return (
      <>
        <button type="button" onClick={() => setPage("/second")}>
          go
        </button>
        <Listing path={page} />
      </>
    );
  }

  render(<Switcher />);
  await waitFor(() => expect(signals.length).toBe(1));
  expect(signals[0].aborted).toBe(false);

  await userEvent.click(screen.getByRole("button", { name: "go" }));
  await waitFor(() => expect(signals.length).toBe(2));

  expect(signals[0].aborted).toBe(true);
  expect(signals[1].aborted).toBe(false);
});

function Watched({ everyMs }: { everyMs: number }) {
  const state = usePanelRead<{ rows: Row[] }>("/watched", 0, everyMs);
  return <Panel state={state}>{(payload) => <span>{payload.rows[0].name}</span>}</Panel>;
}

function answering(held: { name: string }): () => Promise<Response> {
  return async () => json({ rows: [{ name: held.name }] });
}

async function returning() {
  for (const state of ["hidden", "visible"]) {
    Object.defineProperty(document, "visibilityState", { value: state, configurable: true });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
  }
}

test("a read re-reads on the interval it was given, not the one every other pane holds", async () => {
  vi.useFakeTimers();
  onTestFinished(() => {
    vi.useRealTimers();
  });
  const held = { name: "before" };
  vi.stubGlobal("fetch", vi.fn(answering(held)));
  render(<Watched everyMs={1_000} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(screen.getByText("before")).toBeTruthy();

  held.name = "after";
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1_000);
  });

  expect(screen.getByText("after")).toBeTruthy();
});

test("a tab looked at again reads at once rather than serving what it held", async () => {
  const held = { name: "before" };
  const fetched = vi.fn(answering(held));
  vi.stubGlobal("fetch", fetched);
  render(<Watched everyMs={30_000} />);
  expect(await screen.findByText("before")).toBeTruthy();

  held.name = "after";
  await returning();

  expect(await screen.findByText("after")).toBeTruthy();
});

test("the fence answers a failed read with the message and no rows", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("no", { status: 500 })));
  render(<Listing path="/rows" />);
  expect(await screen.findByText("Error 500 — reload to retry.")).toBeTruthy();
  expect(screen.queryByRole("table")).toBeNull();
});

test("the fence answers an empty payload with its own words, not an empty table", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ rows: [] })));
  render(<Listing path="/rows" />);
  expect(await screen.findByText("Nothing listed.")).toBeTruthy();
  expect(screen.queryByRole("table")).toBeNull();
});

test("the table names its columns and one row per record", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ rows: [{ name: "one" }, { name: "two" }] })));
  render(<Listing path="/rows" />);
  await screen.findByText("one");
  expect(screen.getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual(["name"]);
  expect(screen.getAllByRole("row")).toHaveLength(3);
});

test("a card grid lights the whole band of the record standing beside it", async () => {
  const opened: string[] = [];
  const { rerender } = render(
    <CardGrid
      rows={[{ name: "one" }, { name: "two" }]}
      rowKey={(row) => row.name}
      primary={(row) => row.name}
      open={(row) => () => opened.push(row.name)}
      current={(row) => row.name === "one"}
    />,
  );

  const bands = () =>
    screen.getAllByRole("button").map((band) => ({
      name: band.textContent,
      marked: band.getAttribute("aria-current"),
      lit: /(?:^|\s)bg-fill(?:\s|$)/.test(band.className),
    }));

  expect(bands()).toEqual([
    { name: "one", marked: "true", lit: true },
    { name: "two", marked: null, lit: false },
  ]);

  await userEvent.click(screen.getByText("one"));
  expect(opened).toEqual(["one"]);

  rerender(
    <CardGrid
      rows={[{ name: "one" }, { name: "two" }]}
      rowKey={(row) => row.name}
      primary={(row) => row.name}
      open={(row) => () => opened.push(row.name)}
      current={(row) => row.name === "two"}
    />,
  );
  expect(bands()).toEqual([
    { name: "one", marked: null, lit: false },
    { name: "two", marked: "true", lit: true },
  ]);

  rerender(
    <CardGrid
      rows={[{ name: "one" }, { name: "two" }]}
      rowKey={(row) => row.name}
      primary={(row) => row.name}
      open={(row) => () => opened.push(row.name)}
    />,
  );
  expect(bands()).toEqual([
    { name: "one", marked: null, lit: false },
    { name: "two", marked: null, lit: false },
  ]);
});

test("the table falls to its own empty words when it holds no rows", async () => {
  render(
    <DataTable columns={["name"]} rows={[]} rowKey={() => ""} empty="No rows here.">
      {() => <Td />}
    </DataTable>,
  );
  expect(screen.getByText("No rows here.")).toBeTruthy();
  expect(screen.queryByRole("table")).toBeNull();
});

function headed() {
  return render(
    <Header
      heading={1}
      title="Automations"
      acts={<button type="button">New</button>}
    />,
  );
}

test("the header stacks below the narrow breakpoint, so the title keeps its line", () => {
  headed();

  const heading = screen.getByRole("heading", { level: 1, name: "Automations" });
  const band = heading.parentElement!.parentElement!;
  expect(band.className).toContain("max-narrow:flex-col");
  expect(band.className).toContain("max-narrow:items-stretch");
  expect(band.className).toContain("max-narrow:h-auto");

  const controls = screen.getByRole("button", { name: "New" }).parentElement!;
  expect(controls.className).toContain("max-narrow:w-full");
  expect(controls.contains(heading)).toBe(false);
});

/** `flex-1` on the search would let a crowded row take it to its padding, because the flex shorthand
 *  resets the basis it would have shrunk from. */
test("the search stands in the toolbar, and its controls wrap rather than squeeze it", () => {
  render(
    <PageSearch node={<Search label="Search automations" />}>
      <PageToolbar>
        <button type="button">Filter</button>
      </PageToolbar>
    </PageSearch>,
  );

  const toolbar = screen.getByRole("searchbox").closest("form")!.parentElement!;
  expect(toolbar.className).toContain("max-narrow:flex-wrap");
  expect(toolbar.contains(screen.getByRole("button", { name: "Filter" }))).toBe(true);
});

test("the name keeps a floor the acts cannot take", () => {
  headed();

  const name = screen.getByRole("heading", { level: 1, name: "Automations" }).parentElement!;
  expect(name.className).toContain("min-w-(--container-title)");
  expect(name.className).toContain("max-narrow:min-w-0");
  expect(screen.getByRole("button", { name: "New" }).parentElement!.className).toContain(
    "shrink-0",
  );
});

test("the kind's mark stands inside the name's measure, not beside it", () => {
  render(<Header heading={2} title="Reviewer" glyph={<svg aria-hidden />} />);

  const name = screen.getByRole("heading", { level: 2, name: "Reviewer" }).parentElement!;
  expect(name.className).toContain("min-w-(--container-title)");
  expect(name.firstElementChild!.querySelector("svg")).toBeTruthy();
  expect(name.firstElementChild!.className).toContain("[&_svg]:size-(--size-glyph)");
});

test("the lede stands inside the band, under the name it belongs to", () => {
  render(<Header heading={1} title="Meetings" lede="Briefs you before each meeting." />);

  const band = screen
    .getByRole("heading", { level: 1, name: "Meetings" })
    .closest("[data-slot=header]") as HTMLElement;
  const lede = within(band).getByText("Briefs you before each meeting.");
  expect(lede.tagName).toBe("P");
  expect(lede.className).toContain("text-ink-soft");
});

test("a band with no lede draws none", () => {
  render(<Header heading={1} title="Meetings" />);
  const band = screen
    .getByRole("heading", { level: 1, name: "Meetings" })
    .closest("[data-slot=header]") as HTMLElement;
  expect(band.querySelector("p")).toBeNull();
});

test("a header with a crumb and no level names the surface through the crumb alone", () => {
  render(<Header crumb={{ label: "Reviewer", at: "#/agents/reviewer" }} title="Tracked issue" />);

  expect(screen.queryByRole("heading")).toBeNull();
  expect(screen.getByText("Tracked issue").getAttribute("aria-current")).toBe("page");
  expect(screen.getByRole("link", { name: "Back to Reviewer" }).getAttribute("href")).toBe(
    "#/agents/reviewer",
  );
});

test("a header with a crumb makes the crumb's leaf the heading the caller asked for", () => {
  render(<Header heading={1} crumb={{ label: "Wiki" }} title="member@example.com" />);

  const head = screen.getByRole("heading", { level: 1, name: "member@example.com" });
  const path = screen.getByRole("navigation", { name: "Breadcrumb" });
  expect(path.contains(head)).toBe(true);
  expect(path.textContent).toBe("Wiki/member@example.com");
  expect(screen.getByText("member@example.com").getAttribute("aria-current")).toBe("page");
  expect(screen.getAllByRole("heading")).toHaveLength(1);
  expect(screen.queryByRole("link")).toBeNull();
});

test("the inset is the pinned band's, and nothing else's, and neither band draws a line", () => {
  const { container, unmount } = render(<Header heading={2} title="Reviewer" />);
  expect(container.firstElementChild!.className).not.toContain("px-(--size-page-gutter)");
  expect(container.firstElementChild!.className).not.toContain("border-b");
  unmount();

  const pinned = render(<Header heading={2} title="Reviewer" pinned />);
  expect(pinned.container.firstElementChild!.className).toContain("px-(--size-page-gutter)");
  expect(pinned.container.firstElementChild!.className).toContain("py-lg");
  expect(pinned.container.firstElementChild!.className).not.toContain("border-b");
});

test("a lane's band takes the lane's measures, and a screen's takes the page's", () => {
  const lane = render(
    <Header
      pinned
      ruled
      heading={2}
      glyph={<svg aria-hidden />}
      title="Meetings"
      onClose={() => {}}
    />,
  );
  const band = lane.container.firstElementChild!;
  expect(band.className).toContain("px-2xl");
  expect(band.className).toContain("py-sm");
  expect(band.firstElementChild!.className).toContain("h-(--size-control)");

  const named = screen.getByRole("heading", { level: 2, name: "Meetings" });
  expect(named.className).toContain("tracking-(--tracking-ui)");
  expect(named.parentElement!.className).toContain("gap-2xs");

  const shut = screen.getByRole("button", { name: "Close" });
  expect(shut.parentElement!.className).toContain("gap-md");
  expect(shut.className).toContain("size-(--size-glyph)");
  expect(shut.className).toContain("text-ink-soft");
  expect(shut.querySelector("svg")!.getAttribute("stroke-width")).toBe("1.25");
  lane.unmount();

  const page = render(<Header pinned heading={2} title="Meetings" onClose={() => {}} />);
  expect(page.container.firstElementChild!.className).toContain("py-lg");
  const paged = screen.getByRole("heading", { level: 2, name: "Meetings" });
  expect(paged.className).not.toContain("tracking-(--tracking-ui)");
  expect(paged.parentElement!.className).toContain("gap-sm");
  const quiet = screen.getByRole("button", { name: "Close" });
  expect(quiet.parentElement!.className).toContain("gap-sm");
  expect(quiet.className).toContain("size-(--size-control)");
  expect(quiet.className).toContain("hover:bg-fill");
});

test("a band that can be carried takes the cursor of the axis it travels", () => {
  const { container } = render(
    <Header pinned ruled heading={2} title="Meetings" onLift={() => {}} />,
  );

  const band = container.firstElementChild!;
  expect(band.getAttribute("draggable")).toBe("true");
  expect(band.className).toContain("cursor-ew-resize");
});

test("the way out is a glyph and stands last", async () => {
  const shut = vi.fn();
  render(
    <Header heading={2} title="New application" acts={<button type="button">Rebuild</button>} onClose={shut} />,
  );

  const acts = screen.getByRole("button", { name: "Close" });
  expect(acts.textContent).toBe("");
  expect(acts.previousElementSibling!.textContent).toBe("Rebuild");
  expect(acts.parentElement!.lastElementChild).toBe(acts);
  await userEvent.click(acts);
  expect(shut).toHaveBeenCalledOnce();
});

test("an icon act sizes its glyph by the glyph token", () => {
  render(<Header heading={2} title="Reviewer" onClose={() => {}} />);

  expect(screen.getByRole("button", { name: "Close" }).className).toContain(
    "[&_svg]:size-(--size-glyph)",
  );
});

test("a header under a band is its acts, and states nothing the band already states", () => {
  render(
    <Banded value>
      <Header
        pinned
        heading={1}
        glyph={<svg aria-hidden />}
        title="Automations"
        note="Nightly"
        lede="What this app is for."
        bar={<button type="button">Table</button>}
        acts={<button type="button">New</button>}
      />
    </Banded>,
  );

  const row = screen.getByRole("button", { name: "New" }).parentElement!;
  expect(row.getAttribute("data-slot")).toBe("page-acts");
  expect(screen.queryByRole("heading")).toBeNull();
  expect(screen.queryByRole("navigation", { name: "Breadcrumb" })).toBeNull();
  expect(screen.queryByText("Automations")).toBeNull();
  expect(screen.queryByText("Nightly")).toBeNull();
  expect(screen.queryByText("What this app is for.")).toBeNull();
  expect(screen.queryByRole("button", { name: "Table" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Close Automations" })).toBeNull();
  expect(document.querySelector("[data-slot=header]")).toBeNull();
});

/** The act slot is a `display: contents` host that stands empty until a view fills it — a child the row
 *  has and a box it does not — so `:empty` cannot decide this and the row asks for a descendant that draws. */
test("a banded header with nothing to offer draws a row that hides itself", () => {
  const { container, unmount } = render(
    <Banded value>
      <Header pinned heading={1} title="Automations" />
    </Banded>,
  );
  const row = container.firstElementChild!;
  expect(row.getAttribute("data-slot")).toBe("page-acts");
  expect(row.className).toContain("not-has-[:not(.contents)]:hidden");
  expect(row.querySelector("*:not(.contents)")).toBeNull();
  unmount();

  const hosted = render(
    <Banded value>
      <Header pinned heading={1} title="Automations" acts={<span className="contents" />} />
    </Banded>,
  );
  expect(hosted.container.firstElementChild!.querySelector("*:not(.contents)")).toBeNull();
});

test("a header under no band is unchanged", () => {
  render(
    <Banded value={false}>
      <Header pinned heading={1} title="Automations" acts={<button type="button">New</button>} />
    </Banded>,
  );

  expect(screen.getByRole("heading", { level: 1, name: "Automations" })).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "New" }).closest("[data-slot=header]"),
  ).not.toBeNull();
  expect(document.querySelector("[data-slot=page-acts]")).toBeNull();
});

test("the table states a floor covering every track it declares", async () => {
  const columns = [
    { label: "Provider", fact: true },
    "Account",
    "Owner",
    { label: "Access", fact: true },
    { label: "Connected", fact: true },
    "",
  ];
  const { unmount } = render(
    <DataTable columns={columns} rows={[{ name: "one" }]} rowKey={() => "one"} empty="none">
      {() => <Td />}
    </DataTable>,
  );
  expect(declaredFloor(screen.getByRole("table"))).toBe(
    "calc(3 * var(--size-fact-column) + 0 * var(--size-stamp-column) + 3 * var(--size-prose-column) + 0 * var(--size-act))",
  );
  unmount();

  render(
    <DataTable
      columns={[{ label: "Schedule", fact: true }, "Name"]}
      rows={[{ name: "one" }]}
      rowKey={() => "one"}
      empty="none"
      act={() => "Open"}
    >
      {() => <Td />}
    </DataTable>,
  );
  expect(declaredFloor(screen.getByRole("table"))).toBe(
    "calc(1 * var(--size-fact-column) + 0 * var(--size-stamp-column) + 1 * var(--size-prose-column) + 1 * var(--size-act))",
  );
});

/** The head words reach the cells as custom properties, because a cell's generated content cannot reach
 *  the row above it for the word. */
test("the table states its heads for the phone that stacks its rows", () => {
  render(
    <DataTable
      columns={["Member", { label: "Role", fact: true }]}
      rows={[{ name: "one" }]}
      rowKey={() => "one"}
      empty="none"
      act={() => "Open"}
    >
      {() => <Td />}
    </DataTable>,
  );

  const table = screen.getByRole("table");
  expect(table.getAttribute("data-stacks")).toBe("");
  expect(table.style.getPropertyValue("--table-label-1")).toBe('"Member"');
  expect(table.style.getPropertyValue("--table-label-2")).toBe('"Role"');
  expect(table.style.getPropertyValue("--table-label-3")).toBe('""');
});

test("a table the member chose over cards holds its tracks at every width", () => {
  render(
    <DataTable
      columns={["Name", { label: "Type", fact: true }]}
      stacks={false}
      rows={[{ name: "one" }]}
      rowKey={() => "one"}
      empty="none"
      act={() => "Open"}
    >
      {() => <Td />}
    </DataTable>,
  );

  const table = screen.getByRole("table");
  expect(table.getAttribute("data-stacks")).toBeNull();
  expect(table.style.getPropertyValue("--table-label-1")).toBe("");
  expect(declaredFloor(table)).toBe(
    "calc(1 * var(--size-fact-column) + 0 * var(--size-stamp-column) + 1 * var(--size-prose-column) + 1 * var(--size-act))",
  );
});

test("a stamp column takes the narrow track, so a filled table of five still fits its section", () => {
  render(
    <DataTable
      columns={[
        { label: "Name", fill: true },
        { label: "Creator", stamp: true },
        { label: "Events", stamp: true },
        { label: "Next run", stamp: true },
        { label: "Last run", stamp: true },
      ]}
      rows={[{ name: "one" }]}
      rowKey={() => "one"}
      empty="none"
    >
      {() => <Td />}
    </DataTable>,
  );

  const table = screen.getByRole("table");
  expect(declaredFloor(table)).toBe(
    "calc(0 * var(--size-fact-column) + 4 * var(--size-stamp-column) + 1 * var(--size-prose-column) + 0 * var(--size-act))",
  );
  const heads = screen.getAllByRole("columnheader");
  expect(heads[0].className).toContain("w-full");
  for (const head of heads.slice(1)) expect(head.className).toContain("w-(--size-stamp-column)");
});

test("a table with a whole column measures it and holds the tracks beside it", () => {
  render(
    <DataTable
      columns={[{ label: "Name", whole: true }, "Details", { label: "Type", fact: true }]}
      stacks={false}
      rows={[{ name: "one" }]}
      rowKey={() => "one"}
      empty="none"
      act={() => "Open"}
    >
      {() => <Td />}
    </DataTable>,
  );

  const table = screen.getByRole("table");
  expect(table.getAttribute("data-measured")).toBe("");
  expect(table.className).toContain("table-auto");
  expect(table.className).not.toContain("table-fixed");
  const heads = screen.getAllByRole("columnheader");
  expect(heads[0].className).not.toContain("w-(");
  expect(heads[1].className).toContain("w-(--size-prose-column)");
  expect(heads[2].className).toContain("w-(--size-fact-column)");
});

test("the whole cell keeps its name and the prose beside it keeps its bound", () => {
  render(
    <table>
      <tbody>
        <tr>
          <TdWhole>
            <Lede mark={null} whole>
              q3-revenue-review-of-every-region.md
            </Lede>
          </TdWhole>
          <Td>
            <Clip>A run of details the column still cuts</Clip>
          </Td>
        </tr>
      </tbody>
    </table>,
  );

  const cells = screen.getAllByRole("cell");
  expect(cells[0].className).not.toContain("truncate");
  expect(cells[0].className).toContain("whitespace-nowrap");
  expect(screen.getByText("q3-revenue-review-of-every-region.md").className).not.toContain(
    "truncate",
  );
  const clipped = screen.getByText("A run of details the column still cuts");
  expect(clipped.className).toContain("truncate");
  expect(clipped.className).toContain("max-w-(--size-prose-column)");
});

test("the pager draws both steps and disables the one with nowhere to go", async () => {
  const placed: unknown[] = [];
  const { unmount } = render(
    <Pager payload={{ older: "cursor-older" }} onPlace={(next) => placed.push(next)} />,
  );
  expect(screen.getByRole("button", { name: "First" })).toHaveProperty("disabled", true);
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  expect(placed).toEqual([{ after: "cursor-older", opens: undefined }]);
  unmount();

  const { unmount: second } = render(<Pager payload={{ newer: "n", older: "o" }} onPlace={() => {}} />);
  expect(screen.getByRole("button", { name: "Previous" })).toHaveProperty("disabled", false);
  expect(screen.getByRole("button", { name: "Next" })).toHaveProperty("disabled", false);
  second();

  render(<Pager payload={{}} onPlace={() => {}} />);
  expect(screen.queryByRole("button", { name: "Next" })).toBeNull();
});

test("a page standing on a cursor names its back step First, and lands there", async () => {
  const placed: unknown[] = [];
  render(
    <Pager payload={{ older: "o" }} after="cursor-here" onPlace={(next) => placed.push(next)} />,
  );

  expect(screen.queryByRole("button", { name: "Previous" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "First" }));
  expect(placed).toEqual([{ after: undefined, opens: undefined }]);
});

function Form({ schema }: { schema: Parameters<typeof FormFromSchema>[0]["schema"] }) {
  const [values, setValues] = useState<Record<string, SpecValue>>({});
  return (
    <FormFromSchema
      schema={schema}
      values={values}
      onChange={(name, value) => setValues((current) => ({ ...current, [name]: value }))}
    />
  );
}

test("a schema field with an enum becomes a select over exactly its choices", async () => {
  render(
    <Form
      schema={{ properties: { reasoning: { type: "string", enum: ["low", "high"] } } }}
    />,
  );
  expect((await opened("reasoning")).map((option) => option.textContent)).toEqual([
    "Low",
    "High",
  ]);
});

test("a schema field is labelled by its title and states nothing the schema wrote for the agent", () => {
  const wire = {
    type: "string",
    title: "Expires At",
    description: "UTC expiry. Omit on update to preserve it; null clears it.",
  } as SchemaProperty;
  render(<Form schema={{ properties: { expires_at: wire } }} />);
  expect(screen.getByLabelText("Expires At")).toBeTruthy();
  expect(screen.queryByText("expires_at")).toBeNull();
  expect(screen.queryByText(/Omit on update/)).toBeNull();
});

test("a schema field's example is the placeholder, so the shape is shown, not described", () => {
  render(
    <Form
      schema={{
        properties: {
          schedule: {
            title: "Schedule",
            examples: ["0 9 * * 1-5"],
            anyOf: [{ type: "string", maxLength: 100 }, { type: "null" }],
          },
        },
      }}
    />,
  );
  const box = screen.getByLabelText("Schedule") as HTMLInputElement;
  expect(box.placeholder).toBe("0 9 * * 1-5");
  expect(box.maxLength).toBe(100);
});

test("a moment the schema declares takes the browser's own date control", () => {
  render(
    <Form
      schema={{
        properties: {
          expires_at: {
            title: "Expires At",
            anyOf: [{ type: "string", format: "date-time" }, { type: "null" }],
          },
        },
      }}
    />,
  );
  expect(screen.getByLabelText("Expires At").getAttribute("type")).toBe("datetime-local");
});

test("a string the schema declines to bound is prose and takes the taller box", () => {
  render(
    <Form
      schema={{
        properties: {
          prompt: {
            title: "Prompt",
            examples: ["Summarize what merged."],
            anyOf: [{ type: "string" }, { type: "null" }],
          },
          description: {
            title: "Description",
            anyOf: [{ type: "string", maxLength: 120 }, { type: "null" }],
          },
        },
      }}
    />,
  );
  expect(screen.getByLabelText("Prompt").tagName).toBe("TEXTAREA");
  expect((screen.getByLabelText("Prompt") as HTMLTextAreaElement).placeholder).toBe(
    "Summarize what merged.",
  );
  expect(screen.getByLabelText("Description").tagName).toBe("INPUT");
});

test("a schema field with no title falls back to the key the spec names it by", () => {
  render(<Form schema={{ properties: { expires_at: { type: "string" } } }} />);
  expect(screen.getByLabelText("expires_at")).toBeTruthy();
});

test("a boolean field carries its label beside the box, not stacked over it", () => {
  render(<Form schema={{ properties: { paused: { type: "boolean", title: "Paused" } } }} />);
  const box = screen.getByLabelText("Paused");
  expect(box.getAttribute("type")).toBe("checkbox");
  expect(box.parentElement?.className).toContain("items-center");
});

test("a nullable boolean field becomes a checkbox, not a text box", () => {
  render(
    <Form schema={{ properties: { paused: { anyOf: [{ type: "boolean" }, { type: "null" }] } } }} />,
  );
  expect(screen.getByLabelText("paused").getAttribute("type")).toBe("checkbox");
});

test("a boolean field already true arrives checked, not as the string true", () => {
  const prop = { anyOf: [{ type: "boolean" }, { type: "null" }] };
  function Seeded() {
    const [values, setValues] = useState<Record<string, SpecValue>>({
      paused: initialSpecValue(prop, true),
    });
    return (
      <FormFromSchema
        schema={{ properties: { paused: prop } }}
        values={values}
        onChange={(name, value) => setValues((current) => ({ ...current, [name]: value }))}
      />
    );
  }
  render(<Seeded />);
  expect((screen.getByLabelText("paused") as HTMLInputElement).checked).toBe(true);
});

test("a schema field labels itself with the key the member writes in chat", () => {
  render(<Form schema={{ properties: { expires_at: { type: "string" } } }} />);
  expect(screen.getByLabelText("expires_at")).toBeTruthy();
});

test("a read whose body fails mid-stream reports the failure instead of rejecting", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        ({
          ok: true,
          status: 200,
          json: async () => {
            throw new DOMException("The operation was aborted.", "AbortError");
          },
        }) as unknown as Response,
    ),
  );
  await expect(getJson("/whatever")).resolves.toEqual({
    ok: false,
    message: "Network error — try again.",
    status: 0,
  });
});

test("a loading panel draws the shape it is about to fill, not the payload", () => {
  const { container } = render(
    <Panel state={{ phase: "loading" }} shape="cards">{() => <div>never</div>}</Panel>,
  );
  expect(container.querySelectorAll('[data-part="skeleton"]').length).toBeGreaterThan(0);
  expect(screen.queryByText("never")).toBeNull();
});

test("an attention notice is highlighted and a quiet one is not", () => {
  const { container } = render(
    <div>
      <Notice tone="attention">refused</Notice>
      <Notice>done</Notice>
    </div>,
  );
  const [attention, quiet] = Array.from(container.querySelectorAll("div > div > div"));
  expect(attention.className).toContain("bg-attention");
  expect(attention.className).toContain("[color:var(--color-attention-ink)]");
  expect(attention.className).toContain("text-ui");
  expect(attention.getAttribute("role")).toBe("status");
  expect(quiet.className).not.toContain("bg-attention");
  expect(quiet.getAttribute("role")).toBe("status");
});

test("a quiet outcome takes no room until there is an outcome to state", () => {
  const { container, rerender } = render(<OutcomeNotice state={QUIET} />);
  expect(container.firstChild).toBeNull();

  rerender(<OutcomeNotice state={{ text: "Applied.", refused: false }} />);
  expect(screen.getByRole("status").textContent).toBe("Applied.");
});

test("a schema field the model requires says so, and an optional one beside it does not", () => {
  render(
    <Form
      schema={{
        properties: { prompt: { type: "string" }, expires_at: { type: "string" } },
        required: ["prompt"],
      }}
    />,
  );
  expect((screen.getByLabelText("prompt") as HTMLInputElement).required).toBe(true);
  expect((screen.getByLabelText("expires_at") as HTMLInputElement).required).toBe(false);
});

test("a section stacks heading, action bar, then records, and the bar runs from the left", () => {
  render(
    <Section
      title="Members"
      bar={
        <>
          <input aria-label="Search members" />
          <button type="button">Add member</button>
        </>
      }
    >
      roster
    </Section>,
  );
  const heading = screen.getByRole("heading", { name: "Members" });
  const search = screen.getByLabelText("Search members");
  const action = screen.getByRole("button", { name: "Add member" });
  const bar = search.parentElement!;
  expect(bar).toBe(action.parentElement);
  expect(bar.className).not.toContain("justify-between");
  expect(bar.previousElementSibling).toBe(heading.closest("div")?.parentElement);
});

test("a section's action stands at the band's far edge, over the bar and the records", () => {
  render(
    <Section title="Members" action={<a href="https://example.test">Elsewhere</a>} bar={<button type="button">Add member</button>}>
      roster
    </Section>,
  );
  const heading = screen.getByRole("heading", { name: "Members" });
  const out = screen.getByRole("link", { name: "Elsewhere" });
  const band = heading.parentElement!.parentElement!;
  expect(band.lastElementChild).toBe(out.parentElement);
  expect(out.parentElement!.className).toContain("shrink-0");
  expect(heading.parentElement!.className).toContain("flex-1");
  expect(band.previousElementSibling).toBeNull();
  expect(screen.getByRole("button", { name: "Add member" }).closest("section")).toBe(
    band.closest("section"),
  );
});

test("a section's note stands under its heading, not under the bar", () => {
  render(
    <Section title="Credentials" note="A slot is filled in chat." bar={<button type="button">Refresh</button>}>
      rows
    </Section>,
  );
  const heading = screen.getByRole("heading", { name: "Credentials" });
  const note = screen.getByText("A slot is filled in chat.");
  expect(heading.nextElementSibling).toBe(note);
  expect(note.parentElement).toBe(heading.parentElement);
});

test("a table stands on the section's own ground, ruled only between its records", () => {
  render(
    <Table>
      <thead>
        <tr>
          <Th>Member</Th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <Td>lead@example.com</Td>
        </tr>
      </tbody>
    </Table>,
  );
  const frame = screen.getByRole("table").parentElement;
  expect(frame?.className).not.toContain("border-edge");
  expect(frame?.className).not.toContain("rounded-panel");
  const head = screen.getByRole("columnheader", { name: "Member" });
  expect(head.className).not.toContain("border");
  const cell = screen.getByRole("cell", { name: "lead@example.com" });
  expect(cell.className).toContain("border-b");
  expect(cell.className).not.toContain("border-t");
});

test("a row is a band: the cells sit inside the rule that states the row's extent", () => {
  render(
    <Table>
      <tbody>
        <tr>
          <Td>lead@example.com</Td>
        </tr>
      </tbody>
    </Table>,
  );
  const cell = screen.getByRole("cell", { name: "lead@example.com" });
  expect(cell.className).toContain("px-2xl");
  expect(cell.className).toContain("truncate");
});

test("a section with nothing to act on draws no action bar", () => {
  render(<Section title="Members">roster</Section>);
  expect(screen.getByRole("heading", { name: "Members" }).nextElementSibling).toBeNull();
});

test("a section with no action draws no action slot", () => {
  render(<Section title="Members">roster</Section>);
  expect(screen.queryByRole("button")).toBeNull();
});

test("a row's own act fires without opening the row it stands on", async () => {
  const opened: string[] = [];
  const acted: string[] = [];
  render(
    <RowLines
      rows={[{ name: "one" }, { name: "two" }]}
      rowKey={(row) => row.name}
      primary={(row) => row.name}
      meta={() => []}
      open={(row) => () => opened.push(row.name)}
      action={(row) => (
        <Button variant="row" onClick={() => acted.push(row.name)}>
          Remove
        </Button>
      )}
    />,
  );

  await userEvent.click(screen.getAllByRole("button", { name: "Remove" })[0]);
  expect(acted).toEqual(["one"]);
  expect(opened).toEqual([]);

  await userEvent.click(screen.getByRole("button", { name: /^two/ }));
  expect(opened).toEqual(["two"]);
});

test("a row the listing declines to open takes no role and no tab stop", async () => {
  const opened: string[] = [];
  render(
    <RowLines
      rows={[{ name: "shared" }, { name: "walled" }]}
      rowKey={(row) => row.name}
      primary={(row) => row.name}
      meta={() => []}
      open={(row) => (row.name === "walled" ? null : () => opened.push(row.name))}
    />,
  );

  expect(screen.getAllByRole("button").map((row) => row.textContent)).toEqual(["shared"]);
  const walled = screen.getByText("walled").closest("li")!;
  expect(walled.getAttribute("tabindex")).toBeNull();
  expect(walled.className).not.toContain("cursor-pointer");

  await userEvent.click(walled);
  expect(opened).toEqual([]);
});

test("the keyboard opens a row on Enter and on Space", async () => {
  const opened: string[] = [];
  render(
    <RowLines
      rows={[{ name: "one" }]}
      rowKey={(row) => row.name}
      primary={(row) => row.name}
      meta={() => []}
      open={(row) => () => opened.push(row.name)}
    />,
  );

  const row = screen.getByRole("button", { name: "one" });
  row.focus();
  expect(document.activeElement).toBe(row);
  await userEvent.keyboard("{Enter}");
  await userEvent.keyboard(" ");

  expect(opened).toEqual(["one", "one"]);
});

test("under a band, a header naming a step past it draws its crumb and its close", () => {
  const shut = vi.fn();
  render(
    <Banded value>
      <Header
        pinned
        heading={1}
        crumb={{ label: "Radar" }}
        title="morning-digest"
        closes="morning-digest"
        onClose={shut}
      />
    </Banded>,
  );

  expect(screen.getByRole("navigation", { name: "Breadcrumb" }).textContent).toBe(
    "Radar/morning-digest",
  );
  screen.getByRole("button", { name: "Close morning-digest" }).click();
  expect(shut).toHaveBeenCalledOnce();
  expect(document.querySelector("[data-slot=page-acts]")).toBeNull();
});
