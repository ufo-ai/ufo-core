import { render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { ArtifactText } from "@/kernel/artifact";

const CSP =
  "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src data:; style-src 'unsafe-inline'\">";

test("an html artifact renders in a sandboxed iframe", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("<h1>Report</h1>")));

  render(<ArtifactText url="/artifacts/report.html" name="report.html" mediaType="text/html" />);

  const frame = await screen.findByTitle("report.html");
  expect(frame.tagName).toBe("IFRAME");
  expect(frame.getAttribute("sandbox")).toBe("");
  expect(frame.getAttribute("referrerpolicy")).toBe("no-referrer");
  expect(frame.getAttribute("srcdoc")).toBe(CSP + "<h1>Report</h1>");
});

test("an html document past the bound refuses instead of framing", async () => {
  const body = "<p>" + "x".repeat(256 * 1024) + "</p>";
  vi.stubGlobal("fetch", vi.fn(async () => new Response(body)));

  render(<ArtifactText url="/artifacts/big.html" name="big.html" mediaType="text/html" />);

  const refusal = await screen.findByText(
    "This page is larger than 256 kB. Download it to open it.",
  );
  expect(refusal.textContent).toBe("This page is larger than 256 kB. Download it to open it.");
  expect(screen.queryByTitle("big.html")).toBe(null);
});
