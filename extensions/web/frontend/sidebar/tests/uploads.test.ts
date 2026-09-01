import { beforeEach, expect, test, vi } from "vitest";

import { uploadAttachment } from "@/lib/api";

const DIGEST = new Uint8Array(32).fill(7).buffer;
const SHA256 = btoa(String.fromCharCode(...new Uint8Array(DIGEST)));

beforeEach(() => {
  vi.unstubAllGlobals();
  vi.stubGlobal("crypto", { subtle: { digest: async () => DIGEST } });
});

test("the bytes travel to the store measured by the checksum the mint signed", async () => {
  /** The surface signs the size and the sha256 into the URL, so S3 stores exactly the file the
   *  member picked and a URL that leaks buys nothing else. The PUT must therefore carry the same
   *  checksum the mint declared — a body of another length or another content is refused. */
  const calls: [string, RequestInit][] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      calls.push([String(url), init]);
      return String(url).endsWith("/uploads")
        ? new Response(
            JSON.stringify({ key: "web-inbox-uploads/abc/report.pdf", put_url: "https://store/1" }),
            { status: 200 },
          )
        : new Response("", { status: 200 });
    }),
  );

  const file = new File(["a report"], "report.pdf", { type: "application/pdf" });
  expect(await uploadAttachment(file)).toBe("web-inbox-uploads/abc/report.pdf");
  const [mint, put] = calls;
  expect(JSON.parse(String(mint[1].body))).toEqual({
    name: "report.pdf",
    size_bytes: 8,
    sha256: SHA256,
  });
  expect(put[0]).toBe("https://store/1");
  expect(put[1].method).toBe("PUT");
  expect((put[1].headers as Record<string, string>)["x-amz-checksum-sha256"]).toBe(SHA256);
  expect(put[1].body).toBe(file);
});

test("a deploy that signs no upload leaves the file for the send to carry", async () => {
  /** A filesystem dev store mints nothing, so the composer keeps the file inline and the send
   *  rides the body it always has rather than naming a key nothing landed under. */
  const fetched = vi.fn(async () => new Response("", { status: 409 }));
  vi.stubGlobal("fetch", fetched);

  expect(await uploadAttachment(new File(["a report"], "report.pdf"))).toBeNull();
  expect(fetched).toHaveBeenCalledTimes(1);
});
