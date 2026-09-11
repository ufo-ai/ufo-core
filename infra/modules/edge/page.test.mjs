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
  const env = { ORIGIN_BASE: APEX };
  globalThis.fetch = async () => {
    assert.fail("the page drove the worker to call its origin");
  };
  server = createServer(async (incoming, outgoing) => {
    const chunks = [];
    for await (const chunk of incoming) chunks.push(chunk);
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

const JOIN_FORM =
  "https://docs.google.com/forms/d/e/1FAIpQLSeXdDbu8pE64Q2zIf4OYQLz0Gu_ETM7P--4_DjtwRQmKS1FIQ/viewform";

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
    { text: "Join Waitlist", href: JOIN_FORM, target: "_blank", rel: "noopener" },
  ]);
  await page.close();
});

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

test("the public text carries no ufo metaphor", async () => {
  for (const path of ["/privacy", "/slack", "/subprocessors", "/support", "/terms"]) {
    const page = await browser.newPage(DESKTOP);
    await page.goto(`${origin}${path}`);
    assert.doesNotMatch(await page.innerText("body"), BANNED_METAPHOR, path);
    await page.close();
  }
});

const PUBLIC_NAV = [
  ["Slack", "/slack"],
  ["Privacy", "/privacy"],
  ["Terms", "/terms"],
  ["Support", "/support"],
  ["Sign In", "/login"],
];

test("the public shell links the product and its public pages", async () => {
  const page = await browser.newPage(DESKTOP);
  await page.goto(`${origin}/privacy`);
  const brand = page.locator("a.brand");
  assert.equal(await brand.getAttribute("href"), "/");
  assert.equal((await brand.textContent()).trim(), "");
  assert.equal(await brand.locator("img").count(), 1);
  assert.deepEqual(
    await page.locator("nav.site-nav a").evaluateAll((links) =>
      links.map((link) => [link.textContent.trim(), link.getAttribute("href")]),
    ),
    PUBLIC_NAV,
  );
  await page.close();
});

test("the Slack install step text stays beside its number", async () => {
  const page = await browser.newPage(DESKTOP);
  await page.goto(`${origin}/slack`);
  const steps = await page.locator("ol li").evaluateAll((items) =>
    items.map((item) => {
      const range = document.createRange();
      range.selectNodeContents(item);
      const box = item.getBoundingClientRect();
      return {
        expectedLeft: box.left + Number.parseFloat(getComputedStyle(item).paddingLeft) - 1,
        textLeft: Math.min(...[...range.getClientRects()].map((rect) => rect.left)),
      };
    }),
  );
  for (const step of steps) assert.ok(step.textLeft >= step.expectedLeft);
  await page.close();
});

for (const [scheme, expected] of [
  ["light", { surface: "rgb(250, 249, 247)", ink: "rgb(25, 26, 26)" }],
  ["dark", { surface: "rgb(25, 26, 26)", ink: "rgb(245, 245, 245)" }],
]) {
  test(`the public pages use the ${scheme} house palette and local type`, async () => {
    const page = await browser.newPage({ ...DESKTOP, colorScheme: scheme });
    await page.goto(`${origin}/privacy`);
    await page.evaluate(() => document.fonts.ready);
    const styles = await page.evaluate(() => ({
      surface: getComputedStyle(document.body).backgroundColor,
      ink: getComputedStyle(document.body).color,
      bodyFont: getComputedStyle(document.body).fontFamily,
      titleFont: getComputedStyle(document.querySelector("h1")).fontFamily,
      monoLoaded: document.fonts.check('12px "Roboto Mono"'),
      sansLoaded: document.fonts.check('15px "Inter"'),
    }));
    assert.deepEqual(styles, {
      ...expected,
      bodyFont: "Inter, system-ui, sans-serif",
      titleFont: 'Georgia, "Times New Roman", serif',
      monoLoaded: true,
      sansLoaded: true,
    });
    await page.close();
  });
}

for (const path of ["/privacy", "/terms", "/slack"]) {
  test(`${path} fits a 360px-wide screen`, async () => {
    const page = await browser.newPage({
      viewport: { width: 360, height: 800 },
      deviceScaleFactor: 3,
      isMobile: true,
      hasTouch: true,
    });
    await page.goto(`${origin}${path}`);
    const layout = await page.evaluate(() => ({
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      mainLeft: document.querySelector("main").getBoundingClientRect().left,
      mainRight: document.querySelector("main").getBoundingClientRect().right,
      viewport: innerWidth,
      clipped: [...document.body.querySelectorAll("*")]
        .filter((element) => {
          const box = element.getBoundingClientRect();
          return box.width > 0 && (box.left < 0 || box.right > innerWidth);
        })
        .map((element) => element.tagName),
    }));
    assert.equal(layout.overflow, 0);
    assert.ok(layout.mainLeft >= 0);
    assert.ok(layout.mainRight <= layout.viewport);
    assert.deepEqual(layout.clipped, []);
    await page.close();
  });
}

const BANNED_METAPHOR =
  /\bbeam\w*|\btransmit\w*|\bsignals?\b|\bsaucers?\b|\bmothership\b|\bcraft\b|\bfleets?\b|\babduct\w*|\b(un)?identified\b|\bidentification\b|\bobjects?\b/i;

test("every word the page shows carries no ufo metaphor", async () => {
  const page = await open();
  const shown = await page.innerText("body");
  assert.ok(shown.length > 0);
  assert.doesNotMatch(shown, BANNED_METAPHOR);
  await page.close();
});

for (const [where, device] of [
  ["a desktop", DESKTOP],
  ["a phone", PHONE],
]) {
  test(`the Slack page fits ${where} and exposes its install steps`, async () => {
    const page = await browser.newPage(device);
    await page.goto(`${origin}/slack`);
    const content = await page.locator("main").evaluate((main) => ({
      title: main.querySelector("h1")?.textContent,
      steps: [...main.querySelectorAll("ol li")].map((item) => item.textContent.trim()),
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    }));
    assert.equal(content.title, "ufo for Slack");
    assert.equal(content.steps.length, 4);
    assert.equal(content.overflow, 0, `the page scrolls ${content.overflow}px sideways at ${where}`);
    await page.close();
  });
}
