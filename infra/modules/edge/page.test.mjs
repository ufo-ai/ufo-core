import assert from "node:assert/strict";
import { createServer } from "node:http";
import test, { after, before } from "node:test";

import { chromium } from "playwright";

import { importWorker } from "./harness.mjs";

const APEX = "https://flyingobject.ai";

let server;
let browser;
let origin;

before(async () => {
  const worker = await importWorker("page");
  const env = {
    ORIGIN_BASE: APEX,
  };
  // The page's render asks the worker for nothing, so a call out of the worker is a call the
  // suite must see.
  globalThis.fetch = async () => {
    assert.fail("the page drove the worker to call its origin");
  };
  server = createServer(async (incoming, outgoing) => {
    const chunks = [];
    for await (const chunk of incoming) chunks.push(chunk);
    // Presented as the apex: the worker bounces plain-http browsers to https before rendering.
    const reply = await worker.fetch(
      new Request(`${APEX}${incoming.url}`, {
        method: incoming.method,
        headers: incoming.headers,
        body: chunks.length ? Buffer.concat(chunks) : undefined,
      }),
      env,
    );
    outgoing.writeHead(reply.status, Object.fromEntries(reply.headers));
    outgoing.end(Buffer.from(await reply.arrayBuffer()));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch();
});

after(async () => {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
});

const DESKTOP = { viewport: { width: 1200, height: 800 } };
// An iPhone 15-class device: the viewport, pixel density and pointer Safari reports there, so the
// page's coarse-pointer rules resolve the way they do on the phone.
const PHONE = {
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 3,
  isMobile: true,
  hasTouch: true,
};

async function open(device = DESKTOP) {
  const page = await browser.newPage(device);
  page.on("pageerror", (error) => assert.fail(`page error: ${error}`));
  await page.goto(`${origin}/`);
  return page;
}

// The faces and the art are in the document, so the render stands on the document alone. The tab
// mark is the one thing the page reaches for, and it comes off the worker's own route.
test("the page carries its own fonts and art, and reaches only for the mark", async () => {
  const page = await browser.newPage(DESKTOP);
  const asked = [];
  await page.route("**/*", (route) => {
    asked.push(route.request().url());
    return route.continue();
  });
  await page.goto(`${origin}/`);
  await page.evaluate(() => document.fonts.ready);
  const reachable = new Set([`${origin}/`, `${origin}/favicon.svg`, `${origin}/favicon-dark.svg`]);
  assert.deepEqual(
    asked.filter((url) => !reachable.has(url)),
    [],
  );
  assert.ok(asked.includes(`${origin}/`));
  await page.close();
});

// Every face the page inlines is weight the visitor pays for before any text appears, so the set
// is pinned: a face that arrives here has to be one the render reaches, and a face whose
// unicode-range the copy never touches gives itself away as "unloaded".
test("the page inlines no font face its render never reaches", async () => {
  const page = await open();
  await page.evaluate(() => document.fonts.ready);
  const faces = await page.evaluate(() =>
    [...document.fonts].map(({ family, style, status }) => ({ family, style, status })),
  );
  assert.deepEqual(faces, [
    { family: "Canela", style: "normal", status: "loaded" },
    { family: "Canela", style: "italic", status: "loaded" },
    { family: "Inter", style: "normal", status: "loaded" },
  ]);
  await page.close();
});

test("the bar carries the mark and the two account controls, and nothing else", async () => {
  const page = await open();
  const brand = await page.locator("header.topbar a.brand").getAttribute("href");
  assert.equal(brand, "/");
  const actions = await page.locator("header.topbar nav a").evaluateAll((links) =>
    links.map((link) => ({
      text: link.innerText.trim(),
      href: link.getAttribute("href"),
      target: link.getAttribute("target"),
      rel: link.getAttribute("rel"),
    })),
  );
  assert.deepEqual(actions, [
    { text: "Sign In", href: "/login", target: null, rel: null },
    { text: "Sign up", href: "/login", target: null, rel: null },
  ]);
  await page.close();
});

// The display line is the page: on every device it renders whole, on one line, inside the window.
for (const [where, device] of [
  ["a desktop", DESKTOP],
  ["a phone", PHONE],
]) {
  test(`the display line stands whole on ${where}`, async () => {
    const page = await open(device);
    await page.evaluate(() => document.fonts.ready);
    const line = await page.locator("h1.display").evaluate((el) => {
      const range = document.createRange();
      range.selectNodeContents(el);
      const box = el.getBoundingClientRect();
      return {
        text: el.innerText.trim(),
        lines: new Set([...range.getClientRects()].map((rect) => Math.round(rect.top))).size,
        inWindow: box.left >= 0 && box.right <= innerWidth && box.top >= 0,
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      };
    });
    assert.equal(line.lines, 1);
    assert.equal(line.inWindow, true, `the line runs outside the window at ${where}`);
    assert.equal(line.overflow, 0, `the page scrolls ${line.overflow}px sideways at ${where}`);
    assert.equal(line.text, "Build the unknown.");
    await page.close();
  });
}

// The product is named ufo; nothing a member reads plays the part.
const BANNED_METAPHOR =
  /\bbeam\w*|\btransmit\w*|\bsignals?\b|\bsaucers?\b|\bmothership\b|\bcraft\b|\bfleets?\b|\babduct\w*|\b(un)?identified\b|\bidentification\b|\bobjects?\b/i;

test("every word the page shows carries no ufo metaphor", async () => {
  const page = await open();
  const shown = await page.innerText("body");
  assert.ok(shown.length > 0);
  assert.doesNotMatch(shown, BANNED_METAPHOR);
  await page.close();
});
