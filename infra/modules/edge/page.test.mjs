import assert from "node:assert/strict";
import { createServer } from "node:http";
import test, { after, before } from "node:test";

import { chromium } from "playwright";

import { d1, importWorker } from "./harness.mjs";

const APEX = "https://flyingobject.ai";

let server;
let browser;
let origin;
const queued = [];

before(async () => {
  const worker = await importWorker("page");
  const env = {
    DB: d1(),
    ORIGIN_BASE: APEX,
    WAITLIST_EMAILS: {
      async send(message) {
        queued.push(message);
      },
    },
    WAITLIST_DEAD_LETTER_QUEUE: "dead-letters",
  };
  // The gateway is the only outbound call the worker makes; the craft count comes from it.
  globalThis.fetch = async (input) => {
    const url = input instanceof Request ? input.url : String(input);
    if (url.endsWith("/fleet")) return Response.json({ craft: 6 });
    return new Response("origin", { status: 404 });
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

async function open(craft = 1) {
  const page = await browser.newPage({ viewport: { width: 1200, height: 800 } });
  page.on("pageerror", (error) => assert.fail(`page error: ${error}`));
  await page.goto(`${origin}/?n=${craft}`);
  await page.waitForFunction(() => document.querySelector(".craft")?.style.opacity === "1");
  return page;
}

// A press and a release, far enough apart to straddle a redraw, like a hand.
async function press(page, x, y, hold = 140) {
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.waitForTimeout(hold);
  await page.mouse.up();
  await page.waitForTimeout(80);
}

test("the join stands in the middle of the page, above the fleet", async () => {
  const page = await open(6);
  const placed = await page.locator("#join").evaluate((el) => {
    const b = el.getBoundingClientRect();
    const middle = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2);
    return {
      tag: el.tagName,
      drawn: b.width > 0 && b.height > 0,
      offCentre: Math.max(
        Math.abs(b.x + b.width / 2 - innerWidth / 2),
        Math.abs(b.y + b.height / 2 - innerHeight / 2),
      ),
      reachable: middle?.closest("#join") === el,
    };
  });
  assert.equal(placed.tag, "FORM");
  assert.equal(placed.drawn, true);
  assert.ok(placed.offCentre < 1, `the block sits ${placed.offCentre}px off centre`);
  assert.equal(placed.reachable, true);
  await page.close();
});

test("a craft is drawn, not a door", async () => {
  const page = await open(6);
  for (let attempt = 0; attempt < 6; attempt++) {
    const clearCraft = await page.waitForFunction(() => {
      const block = document.getElementById("join").getBoundingClientRect();
      for (const el of document.querySelectorAll(".craft")) {
        const b = el.getBoundingClientRect();
        const clearOfBlock =
          b.right < block.left ||
          b.left > block.right ||
          b.bottom < block.top ||
          b.top > block.bottom;
        if (!clearOfBlock) continue;
        const x = b.x + b.width / 2;
        const y = b.y + b.height / 2;
        return { x, y, onCraft: Boolean(document.elementFromPoint(x, y)?.closest(".craft")) };
      }
      return null;
    });
    const spot = await clearCraft.jsonValue();
    assert.equal(spot.onCraft, false);
    await press(page, spot.x, spot.y, 60 + attempt * 25);
    assert.equal(await page.evaluate(() => document.activeElement.tagName), "BODY");
    assert.equal(await page.textContent("#ack"), "");
  }
  await page.close();
});

test("the panel copy is the fixed join: Title Case label, terminal prompt, join verb", async () => {
  const page = await open();
  assert.equal((await page.textContent("#join .head")).trim(), "Join Waitlist");
  assert.equal(await page.getAttribute("#join", "aria-labelledby"), "join-head");
  assert.equal(await page.getAttribute("#join .head", "id"), "join-head");
  assert.equal(await page.getAttribute("#email", "placeholder"), "email@work.com");
  assert.equal((await page.textContent("#join .prompt")).trim(), ">");
  // The prompt is plain terminal text: the panel's own color, no glow.
  const prompt = await page.locator("#join .prompt").evaluate((el) => ({
    shadow: getComputedStyle(el).textShadow,
    panelColor: getComputedStyle(el).color === getComputedStyle(el.closest(".panel")).color,
  }));
  assert.deepEqual(prompt, { shadow: "none", panelColor: true });
  // The entry's insertion caret carries no color of its own.
  const caret = await page.locator("#email").evaluate((el) => ({
    caret: getComputedStyle(el).caretColor,
    color: getComputedStyle(el).color,
  }));
  assert.ok(caret.caret === "auto" || caret.caret === caret.color, caret.caret);
  assert.equal((await page.textContent("#go")).trim(), "join");
  await page.close();
});

test("the join posts to the worker and renders its ack verbatim", async () => {
  const page = await open();
  const before = queued.length;
  await page.fill("#email", "Pilot@YourCo.com");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() =>
    document.getElementById("ack").textContent.includes("waitlist"),
  );
  assert.match(
    (await page.textContent("#ack")).trim(),
    /^#\d+ on the waitlist\. We will email you when access opens\.$/,
  );
  assert.equal(await page.locator("#email").isDisabled(), true);
  assert.equal(await page.locator("#go").isDisabled(), true);
  assert.equal(queued.length, before + 1);
  assert.equal(queued.at(-1).email, "pilot@yourco.com");
  await page.close();
});

test("an address the browser rejects never reaches the worker", async () => {
  const page = await open();
  let posts = 0;
  page.on("request", (r) => r.url().endsWith("/waitlist") && r.method() === "POST" && posts++);
  await page.fill("#email", "not-an-address");
  await page.click("#go");
  await page.waitForTimeout(300);
  assert.equal(posts, 0);
  assert.equal(await page.textContent("#ack"), "");
  await page.close();
});

test("an address the browser allows but the worker refuses can be corrected", async () => {
  const page = await open();
  let posted = 0;
  page.on("request", (r) => r.url().endsWith("/waitlist") && r.method() === "POST" && posted++);
  // Native email validation does not require a dot in the domain. The worker's regex does, so
  // this reaches the wire and comes back a 400 — the path the two rules disagree on.
  await page.fill("#email", "pilot@localhost");
  await page.click("#go");
  await page.waitForFunction(() =>
    document.getElementById("ack").textContent.includes("not accepted"),
  );
  assert.equal(posted, 1);
  assert.equal(await page.textContent("#ack"), "That email address was not accepted.");
  assert.equal(await page.locator("#ack").evaluate((el) => el.classList.contains("bad")), true);
  // Nothing was logged, so the field stays open and the second try succeeds.
  assert.equal(await page.locator("#email").isDisabled(), false);
  await page.fill("#email", "pilot@yourco.com");
  await page.click("#go");
  await page.waitForFunction(() =>
    document.getElementById("ack").textContent.includes("waitlist"),
  );
  assert.equal(await page.locator("#ack").evaluate((el) => el.classList.contains("bad")), false);
  assert.equal(await page.locator("#email").isDisabled(), true);
  await page.close();
});

test("a body that dies after its headers hands the button back", async () => {
  const page = await open();
  await page.evaluate(() => {
    window.fetch = async () => ({ ok: true, text: () => Promise.reject(new Error("stream died")) });
  });
  await page.fill("#email", "cut@off.com");
  await page.click("#go");
  await page.waitForFunction(() => !document.getElementById("go").disabled, {
    timeout: 3000,
  });
  assert.equal(await page.textContent("#ack"), "Request failed. Try again.");
  assert.equal(await page.locator("#email").isDisabled(), false);
  await page.close();
});

test("the entry is the first tab stop and nothing takes the block away", async () => {
  const page = await open();
  await page.keyboard.press("Tab");
  assert.equal(await page.evaluate(() => document.activeElement.id), "email");
  await page.keyboard.type("typed@yourco.com");
  await page.keyboard.press("Escape");
  await page.waitForTimeout(150);
  assert.equal(await page.locator("#join").isVisible(), true);
  assert.equal(await page.inputValue("#email"), "typed@yourco.com");
  await page.close();
});

// The craft is the mark and stays: the copy rule bans metaphor words, never the art. Glyph counts
// measured across 2400 samples of 20 craft mid-morph — min 11, median 24, never 0 — so a craft
// emptied while keeping its class and opacity fails here rather than passing unnoticed.
const CRAFT_MIN_GLYPHS = 8;

test("every craft renders as drawn glyphs", async () => {
  const page = await open(4);
  const drawn = await page.$$eval(".craft", (els) =>
    els.map((el) => (el.textContent.match(/\S/g) ?? []).length),
  );
  assert.equal(drawn.length, 4);
  for (const glyphs of drawn) {
    assert.ok(glyphs >= CRAFT_MIN_GLYPHS, `a craft rendered only ${glyphs} glyphs`);
  }
  await page.close();
});

// The product is named ufo; nothing a member reads plays the part. Swept from the rendered page
// rather than landing.html's source, because the retained ASCII art engine names saucer, mothership,
// craft and beams in its own code — the copy is what a browser puts on screen.
const BANNED_METAPHOR =
  /\bbeam\w*|\btransmit\w*|\bsignals?\b|\bsaucers?\b|\bmothership\b|\bcraft\b|\bfleets?\b|\babduct\w*|\b(un)?identified\b|\bidentification\b|\bobjects?\b/i;

// Submit and return whatever the ack becomes — never waiting on a word, so rewording the copy
// under test surfaces as a lexicon failure rather than a timeout.
async function ackAfter(page, address) {
  const before = await page.textContent("#ack");
  await page.fill("#email", address);
  await page.click("#go");
  await page.waitForFunction(
    (was) => {
      const now = document.getElementById("ack").textContent;
      return now.length > 0 && now !== was && now !== "Joining…";
    },
    before,
    { timeout: 5000 },
  );
  return page.textContent("#ack");
}

test("every word the page shows carries no ufo metaphor", async () => {
  const page = await open();
  const shown = [await page.innerText("body")];
  shown.push(await page.innerText("#join"));
  shown.push(await page.getAttribute("#email", "placeholder"));
  // The worker's 400 copy: native validation passes a dotless domain, its regex does not.
  shown.push(await ackAfter(page, "pilot@localhost"));
  // The dead-body path, the one ack no server can produce.
  await page.evaluate(() => {
    window.fetch = async () => ({ ok: true, text: () => Promise.reject(new Error("cut")) });
  });
  shown.push(await ackAfter(page, "cut@lexicon.com"));
  await page.reload();
  await page.waitForFunction(() => document.querySelector(".craft")?.style.opacity === "1");
  shown.push(await ackAfter(page, "lexicon@yourco.com"));
  assert.ok(shown.length === 6 && shown.every((copy) => copy && copy.length > 0), shown.join("|"));
  for (const copy of shown) {
    assert.doesNotMatch(copy, BANNED_METAPHOR);
  }
  await page.close();
});
