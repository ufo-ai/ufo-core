// The landing page's behaviour, executed. A browser is the real dependency here: the panel's
// release rule is geometric and the craft are clickable only because of how the morph redraws
// them — neither survives being asserted against the HTML source.
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
const isOpen = (page) => page.locator("#panel").evaluate((d) => d.open);

// A press and a release, far enough apart to straddle a redraw, like a hand.
async function press(page, x, y, hold = 140) {
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.waitForTimeout(hold);
  await page.mouse.up();
  await page.waitForTimeout(80);
}
const centreOf = (page, selector) =>
  page.locator(selector).first().evaluate((el) => {
    const b = el.getBoundingClientRect();
    return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
  });

test("a craft opens the panel even though the morph redraws it mid-click", async () => {
  const page = await open(6);
  let opened = 0;
  for (let attempt = 0; attempt < 12; attempt++) {
    const spot = await centreOf(page, ".craft");
    await press(page, spot.x, spot.y, 60 + attempt * 25);
    if (await isOpen(page)) {
      opened++;
      await page.keyboard.press("Escape");
      await page.waitForTimeout(100);
    }
  }
  // Hit-testing the glyphs instead of the craft drops this to roughly a quarter.
  assert.equal(opened, 12);
  await page.close();
});

test("empty sky is not a door", async () => {
  const page = await open(2);
  for (let attempt = 0; attempt < 8; attempt++) {
    const empty = await page.evaluate(() => {
      for (let tries = 0; tries < 500; tries++) {
        const x = 40 + Math.random() * 1120;
        const y = 40 + Math.random() * 600;
        const el = document.elementFromPoint(x, y);
        if (el && !el.closest(".craft") && el.id !== "hail") return { x, y };
      }
      return null;
    });
    if (!empty) continue;
    await press(page, empty.x, empty.y, 90);
    assert.equal(await isOpen(page), false);
  }
  await page.close();
});

test("a click in the panel's own padding does not discard what was typed", async () => {
  const page = await open();
  await page.click("#hail");
  await page.fill("#email", "half@typed.com");
  const edge = await page.locator("#panel").evaluate((el) => {
    const b = el.getBoundingClientRect();
    return { x: b.x + 6, y: b.y + 6 };
  });
  // The padding belongs to the dialog element, so this is the click the target check got wrong.
  assert.equal(
    await page.evaluate(([x, y]) => document.elementFromPoint(x, y).tagName, [edge.x, edge.y]),
    "DIALOG",
  );
  await press(page, edge.x, edge.y, 90);
  assert.equal(await isOpen(page), true);
  assert.equal(await page.inputValue("#email"), "half@typed.com");
  await page.close();
});

test("a click on the backdrop releases you", async () => {
  const page = await open();
  await page.click("#hail");
  const outside = await page.locator("#panel").evaluate((el) => {
    const b = el.getBoundingClientRect();
    return { x: b.x / 2, y: b.y / 2 };
  });
  await press(page, outside.x, outside.y, 90);
  assert.equal(await isOpen(page), false);
  await page.close();
});

test("the join posts to the worker and renders its ack verbatim", async () => {
  const page = await open();
  const before = queued.length;
  await page.click("#hail");
  await page.fill("#email", "Pilot@YourCo.com");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() =>
    document.getElementById("ack").textContent.includes("waitlist"),
  );
  assert.match(
    (await page.textContent("#ack")).trim(),
    /^#\d+ on the waitlist\. we will email you when access opens\.$/,
  );
  assert.equal(await page.locator("#email").isDisabled(), true);
  assert.equal(queued.length, before + 1);
  assert.equal(queued.at(-1).email, "pilot@yourco.com");
  await page.close();
});

test("an address the browser rejects never reaches the worker", async () => {
  const page = await open();
  await page.click("#hail");
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
  await page.click("#hail");
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
  assert.equal(await page.textContent("#ack"), "that email address was not accepted.");
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
  await page.click("#hail");
  await page.evaluate(() => {
    window.fetch = async () => ({ ok: true, text: () => Promise.reject(new Error("stream died")) });
  });
  await page.fill("#email", "cut@off.com");
  await page.click("#go");
  await page.waitForFunction(() => !document.getElementById("go").disabled, {
    timeout: 3000,
  });
  assert.equal(await page.textContent("#ack"), "request failed. try again.");
  assert.equal(await page.locator("#email").isDisabled(), false);
  await page.close();
});

test("the join is reachable without a pointer, and Escape leaves", async () => {
  const page = await open();
  await page.keyboard.press("Tab");
  assert.equal(await page.evaluate(() => document.activeElement.id), "hail");
  await page.keyboard.press("Enter");
  await page.waitForTimeout(150);
  assert.equal(await isOpen(page), true);
  assert.equal(await page.evaluate(() => document.activeElement.id), "email");
  await page.keyboard.press("Escape");
  await page.waitForTimeout(150);
  assert.equal(await isOpen(page), false);
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

// Curiosity is measured in frames the page actually rendered, never in elapsed time: the pull
// advances once per frame, so charging it for frames a loaded machine never delivered measures the
// runner instead of the animation. 900 frames of drift is ~45 simulated seconds at the dt cap,
// against a gather that takes 1-365 frames.
const CURSOR = { x: 600, y: 400 };
const NEAR_RADIUS_PX = 120;
const GATHER_FRAMES = 900;
const STAY_FRAMES = 90;
const STAY_SHARE = 0.8;
// A frame budget cannot bound a loop that is never handed a frame: if rAF stalls outright, nothing
// inside the page runs to notice. This is the liveness backstop, never the measurement — it fails
// with a message instead of hanging on the job's 15-minute ceiling. The test itself measures ~2s.
const STALL_BACKSTOP = { timeout: 120_000 };

test("craft close on a held cursor rather than fleeing it", STALL_BACKSTOP, async () => {
  const page = await open(10);
  await page.mouse.move(CURSOR.x, CURSOR.y);
  const gathered = await page.evaluate(
    async ({ x, y, radius, gatherFrames, stayFrames }) => {
      const nearest = () =>
        Math.min(
          ...[...document.querySelectorAll(".craft")]
            .filter((el) => Number(el.style.opacity) > 0.3)
            .map((el) => {
              const b = el.getBoundingClientRect();
              return Math.hypot(b.x + b.width / 2 - x, b.y + b.height / 2 - y);
            }),
        );
      const frame = () => new Promise(requestAnimationFrame);
      let arrived = null;
      for (let count = 1; count <= gatherFrames && arrived === null; count++) {
        await frame();
        if (nearest() < radius) arrived = count;
      }
      if (arrived === null) return { arrived, stayed: 0 };
      let near = 0;
      for (let count = 0; count < stayFrames; count++) {
        await frame();
        if (nearest() < radius) near++;
      }
      return { arrived, stayed: near / stayFrames };
    },
    {
      ...CURSOR,
      radius: NEAR_RADIUS_PX,
      gatherFrames: GATHER_FRAMES,
      stayFrames: STAY_FRAMES,
    },
  );
  // Under the avoidance this replaced, a craft could not sit inside the cursor's radius at all.
  assert.ok(
    gathered.arrived !== null,
    `no craft reached ${NEAR_RADIUS_PX}px of the cursor in ${GATHER_FRAMES} frames`,
  );
  // Arriving once could be a flyby; curiosity holds it there.
  assert.ok(
    gathered.stayed > STAY_SHARE,
    `a craft was near the cursor in only ${(gathered.stayed * 100).toFixed(0)}% of frames`,
  );
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
      return now.length > 0 && now !== was && now !== "joining…";
    },
    before,
    { timeout: 5000 },
  );
  return page.textContent("#ack");
}

test("every word the page shows carries no ufo metaphor", async () => {
  const page = await open();
  const shown = [await page.innerText("body")];
  await page.click("#hail");
  shown.push(await page.innerText("#panel"));
  shown.push(await page.getAttribute("#email", "placeholder"));
  shown.push(await page.getAttribute("#panel", "aria-label"));
  // The worker's 400 copy: native validation passes a dotless domain, its regex does not.
  shown.push(await ackAfter(page, "pilot@localhost"));
  // The dead-body path, the one ack no server can produce.
  await page.evaluate(() => {
    window.fetch = async () => ({ ok: true, text: () => Promise.reject(new Error("cut")) });
  });
  shown.push(await ackAfter(page, "cut@lexicon.com"));
  await page.reload();
  await page.waitForFunction(() => document.querySelector(".craft")?.style.opacity === "1");
  await page.click("#hail");
  shown.push(await ackAfter(page, "lexicon@yourco.com"));
  assert.ok(shown.length === 7 && shown.every((copy) => copy && copy.length > 0), shown.join("|"));
  for (const copy of shown) {
    assert.doesNotMatch(copy, BANNED_METAPHOR);
  }
  await page.close();
});
