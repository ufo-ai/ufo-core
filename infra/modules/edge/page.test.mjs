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

const DESKTOP = { viewport: { width: 1200, height: 800 } };
// The same window pulled short, where the block's 5vh of sky measures 30px instead of 40px.
const DESKTOP_SHORT = { viewport: { width: DESKTOP.viewport.width, height: 600 } };
// And pulled tall, where 5vh wants 60px and only the clamp's ceiling holds the sky to 48px. It takes
// this much window: at 960px 5vh measures 48px on the nose, so a ceiling raised to any number above
// 48 would still measure 48 there and hold nothing back.
const DESKTOP_TALL = { viewport: { width: DESKTOP.viewport.width, height: 1200 } };
// An iPhone 15-class device: the viewport, pixel density and pointer Safari reports there, so the
// page's coarse-pointer rules resolve the way they do on the phone.
const PHONE = {
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 3,
  isMobile: true,
  hasTouch: true,
};
// The same phone turned on its side: a coarse pointer on a viewport wider than a phone-width query
// would ever cover, which is why the page keys the rules off the pointer and not the width.
const PHONE_LANDSCAPE = {
  ...PHONE,
  viewport: { width: PHONE.viewport.height, height: PHONE.viewport.width },
};
// A landscape window 568px across, shorter than any phone is served in: the block a coarse pointer
// gets stands 172.39px once a print has armed the ack's strip, and stands whole in the 250px window
// Safari's own chrome leaves, so the band where the cap binds lies below that — the sky takes
// 20px above and below the block, so every window under 212px caps a block that has printed, and
// under 177px the ack's own printed copy is part of what the cap holds back. The compact 107.59px
// the block rests at clears every window in the band, which is why the legs below read the cap after
// a report and not before one. This is the top of the band the three cap legs walk, and the height
// the roll is read at. The width is load-bearing as much as the height — the ack wraps to two lines
// here and to three at 320px — so both are stated outright rather than spread from PHONE.
const PHONE_LANDSCAPE_SHORT = { ...PHONE, viewport: { width: 568, height: 170 } };
// The narrowest phone still in service is 320 CSS px across, which is where the worker's success ack
// wraps to three lines — the height the strip reserves — and where the row has the least width to
// divide between the entry and the button.
const PHONE_NARROW = { ...PHONE, viewport: { width: 320, height: 568 } };

// `before` runs in the page before any of its own script does, which is the only place a browser's
// own API can be taken away from it, or already be reporting a keyboard when the page first reads it.
async function open(craft = 1, device = DESKTOP, before, beforeArg) {
  const page = await browser.newPage(device);
  page.on("pageerror", (error) => assert.fail(`page error: ${error}`));
  if (before) await page.addInitScript(before, beforeArg);
  await page.goto(`${origin}/?n=${craft}`);
  await page.waitForFunction(() => document.querySelector(".craft")?.style.opacity === "1");
  return page;
}

// Everything a finger or a Tab can land on inside the block.
const FOCUSABLE = "input, button, select, textarea, a[href], [tabindex]";

function controls(page) {
  return page.locator("#join").evaluate(
    (block, selector) =>
      [...block.querySelectorAll(selector)].map((el) => {
        const box = el.getBoundingClientRect();
        return {
          id: el.id,
          fontSize: Number.parseFloat(getComputedStyle(el).fontSize),
          height: box.height,
          left: box.left,
          right: box.right,
        };
      }),
    FOCUSABLE,
  );
}

// Safari zooms the page in when the control taking focus computes under 16px, and 44px is a
// fingertip's worth of target.
const PHONE_MIN_FONT = 16;
const PHONE_MIN_TARGET = 44;

function assertSizedForAFinger(measured) {
  for (const control of measured) {
    assert.ok(
      control.fontSize >= PHONE_MIN_FONT,
      `#${control.id} computes to ${control.fontSize}px`,
    );
    assert.ok(
      control.height >= PHONE_MIN_TARGET,
      `#${control.id} is ${control.height}px to a fingertip`,
    );
  }
}

// What the ack takes and what the strip holds for it, in lines. The ack's own lines are the distinct
// tops of the rectangles a range over its copy covers — its line boxes — because the strip's
// reserved height is the floor under scrollHeight and would answer for the copy otherwise.
const ackRoom = (page) =>
  page.locator("#ack").evaluate((el) => {
    const range = document.createRange();
    range.selectNodeContents(el);
    const line = Number.parseFloat(getComputedStyle(el).lineHeight);
    return {
      lines: new Set([...range.getClientRects()].map((r) => Math.round(r.top))).size,
      reserved: Math.round(Number.parseFloat(getComputedStyle(el).minHeight) / line),
    };
  });

const boxOf = (page, selector) =>
  page.locator(selector).evaluate((el) => {
    const b = el.getBoundingClientRect();
    return {
      x: Math.round(b.x),
      y: Math.round(b.y),
      width: Math.round(b.width),
      height: Math.round(b.height),
    };
  });

// A press and a release, far enough apart to straddle a redraw, like a hand.
async function press(page, x, y, hold = 140) {
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.waitForTimeout(hold);
  await page.mouse.up();
  await page.waitForTimeout(80);
}

// The ack is the block's last line and its tallest copy, and nothing prints in it until a hand
// acts, so every case that could clip it is measured with one landed.
async function landAJoin(page, address) {
  await page.fill("#email", address);
  await page.click("#go");
  await page.waitForFunction(() =>
    document.getElementById("ack").textContent.includes("waitlist"),
  );
}

// The panel caps its own height, so the window's edge is no longer the only thing that can hide a
// line: copy the cap holds back lies inside the window and outside the panel's scroll port, which
// is what a member sees through. Every box that has to be read or pressed is measured against
// both, and the box's own height is what wholly shown means.
//
// The window here is the window a member sees, which is the visual viewport: an open keyboard leaves
// the layout viewport untouched, so a line standing behind a keyboard stands inside `innerHeight` all
// the same. With no keyboard and no zoom the two are the same strip to the pixel on every device the
// suite drives, so this reads what it always read there.
const shownIn = (page, ...selectors) =>
  page.locator("#join").evaluate((panel, wanted) => {
    const to2 = (n) => Math.round(n * 100) / 100;
    const outer = panel.getBoundingClientRect();
    const clipTop = outer.top + panel.clientTop;
    const clipBottom = clipTop + panel.clientHeight;
    const viewport = window.visualViewport;
    const seenTop = viewport ? viewport.offsetTop : 0;
    const seenBottom = seenTop + (viewport ? viewport.height : innerHeight);
    const spanned = (box, top, bottom) =>
      to2(Math.max(0, Math.min(box.bottom, bottom) - Math.max(box.top, top)));
    return Object.fromEntries(
      wanted.map((selector) => {
        const box = panel.querySelector(selector).getBoundingClientRect();
        return [
          selector,
          {
            height: to2(box.height),
            inClip: spanned(box, clipTop, clipBottom),
            inWindow: spanned(box, seenTop, seenBottom),
          },
        ];
      }),
    );
  }, selectors);

// What a member can read of the ack, as against what the strip reserves for it: its box is taller
// than its copy wherever the copy is shorter, and the box is not what is read. The printed line boxes
// are — the rectangles ackRoom counts — measured against the same two bounds as any other part, and
// shaped for assertWhollyShown. Empty is empty: nothing printed measures nothing.
const ackCopyShownIn = (page) =>
  page.locator("#join").evaluate((panel) => {
    const to2 = (n) => Math.round(n * 100) / 100;
    const range = document.createRange();
    range.selectNodeContents(panel.querySelector("#ack"));
    const printed = [...range.getClientRects()];
    if (!printed.length) return {};
    const copy = { top: printed[0].top, bottom: printed.at(-1).bottom };
    const outer = panel.getBoundingClientRect();
    const clipTop = outer.top + panel.clientTop;
    const viewport = window.visualViewport;
    const seenTop = viewport ? viewport.offsetTop : 0;
    const seenBottom = seenTop + (viewport ? viewport.height : innerHeight);
    const spanned = (top, bottom) =>
      to2(Math.max(0, Math.min(copy.bottom, bottom) - Math.max(copy.top, top)));
    return {
      "the ack's printed copy": {
        height: to2(copy.bottom - copy.top),
        inClip: spanned(clipTop, clipTop + panel.clientHeight),
        inWindow: spanned(seenTop, seenBottom),
      },
    };
  });

// A control the page left enabled is a control a member is still expected to use, so it is the set a
// bound window has to keep whole — read off the page rather than assumed, because only one of
// report()'s four paths takes the entry away.
const liveControls = (page) =>
  page
    .locator("#join")
    .evaluate((block) => ["#email", "#go"].filter((id) => !block.querySelector(id).disabled));

function assertWhollyShown(shown, where) {
  for (const [selector, box] of Object.entries(shown)) {
    assert.deepEqual(
      { inClip: box.inClip, inWindow: box.inWindow },
      { inClip: box.height, inWindow: box.height },
      `${selector} shows ${box.inClip}px inside the panel's cap and ${box.inWindow}px inside the ` +
        `window, of ${box.height}px, at ${where}`,
    );
  }
}

// 5vh of sky below the block, clamped between these two.
const BOTTOM_GAP_MIN = 20;
const BOTTOM_GAP_MAX = 48;

const placementOf = (page) =>
  page.locator("#join").evaluate((el) => {
    const b = el.getBoundingClientRect();
    const middle = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2);
    return {
      tag: el.tagName,
      drawn: b.width > 0 && b.height > 0,
      offCentreX: Math.abs(b.x + b.width / 2 - innerWidth / 2),
      gap: innerHeight - b.bottom,
      top: b.top,
      belowMiddle: b.top > innerHeight / 2,
      // the panel caps its height and scrolls, so its own cap can put copy out of sight on a
      // viewport whose edge does not
      scrolled: el.scrollHeight > el.clientHeight + 1,
      reachable: middle?.closest("#join") === el,
    };
  });

// A scroll under a hand rather than under a script, which is the difference between a panel that
// caps its copy and one that hides it: a panel that hides it still answers the page's own scroll of
// a landed ack, and only a wheel notices. The wheel starts from wherever the page left the panel —
// after a print that is the scroll the copy needed, not the head — so what comes back measures the
// hand only against the scroll it began at.
async function rollThePanel(page, by) {
  const panel = await boxOf(page, "#join");
  await page.mouse.move(panel.x + panel.width / 2, panel.y + panel.height / 2);
  await page.mouse.wheel(0, by);
  await page.waitForTimeout(120);
  return page.locator("#join").evaluate((el) => el.scrollTop);
}

test("the join stands at the foot of the page, above the fleet", async () => {
  const page = await open(6);
  const placed = await placementOf(page);
  assert.equal(placed.tag, "FORM");
  assert.equal(placed.drawn, true);
  assert.ok(placed.offCentreX < 1, `the block sits ${placed.offCentreX}px off centre`);
  assert.ok(
    placed.gap >= BOTTOM_GAP_MIN && placed.gap <= BOTTOM_GAP_MAX,
    `${placed.gap}px between the block and the bottom edge`,
  );
  assert.equal(placed.belowMiddle, true);
  assert.equal(placed.reachable, true);
  await page.close();
});

// iOS Safari zooms the page in when the control taking focus computes under 16px, which is the
// blown-up, clipped block a phone showed after tapping the entry. Chromium cannot reproduce that
// zoom, so the computed size is the assertion.
test("every focusable control in the block is at least 16px on a phone", async () => {
  const page = await open(6, PHONE);
  const resting = await controls(page);
  assert.deepEqual(
    resting.map((control) => control.id),
    ["email", "go"],
  );
  await page.locator("#email").focus();
  assert.equal(await page.evaluate(() => document.activeElement.id), "email");
  const focused = await controls(page);
  assertSizedForAFinger([...resting, ...focused]);
  assert.deepEqual(focused, resting);
  await page.close();
});

// A phone in landscape is the case no width query serves: 844px across, still a coarse pointer,
// still zooming Safari in on a 14px control. Retarget the page's rule to a phone-sized max-width
// and this fails while the portrait test above stays green.
test("every focusable control in the block is at least 16px on a phone in landscape", async () => {
  const page = await open(6, PHONE_LANDSCAPE);
  assert.deepEqual(
    await page.evaluate(() => [innerWidth, innerHeight]),
    [PHONE_LANDSCAPE.viewport.width, PHONE_LANDSCAPE.viewport.height],
  );
  const measured = await controls(page);
  assert.deepEqual(
    measured.map((control) => control.id),
    ["email", "go"],
  );
  assertSizedForAFinger(measured);
  await page.close();
});

test("the desktop panel keeps its 14px terminal size", async () => {
  const page = await open();
  assert.equal(await page.locator("#join").evaluate((el) => getComputedStyle(el).fontSize), "14px");
  for (const control of await controls(page)) {
    assert.equal(control.fontSize, 14, `#${control.id} computes to ${control.fontSize}px`);
  }
  await page.close();
});

// Both short viewports, because the coarse-pointer rules carry 16px type and 44px targets on the
// one and the desktop panel keeps its own looser spacing — and so the taller block — on the other.
test("a short viewport shows the whole block, entry included", async () => {
  for (const device of [DESKTOP_SHORT, PHONE_LANDSCAPE]) {
    const page = await open(6, device);
    const { width, height } = page.viewportSize();
    const where = `${width}x${height}`;
    const placed = await placementOf(page);
    assert.ok(placed.top >= 0, `the block is cut off by ${-placed.top}px at ${where}`);
    assert.ok(
      placed.gap >= BOTTOM_GAP_MIN && placed.gap <= BOTTOM_GAP_MAX,
      `${placed.gap}px between the block and the bottom edge at ${where}`,
    );
    assert.equal(placed.scrolled, false, `the block scrolls its own copy out of sight at ${where}`);
    assert.equal(placed.reachable, true);
    assertWhollyShown(await shownIn(page, "#email", "#go", "#ack"), where);
    // Again with the ack's copy in it: the reserved room is one thing, the landed line another, and
    // the cap is what would take the difference.
    await landAJoin(page, `short-${height}@yourco.com`);
    const landed = await placementOf(page);
    assert.equal(landed.scrolled, false, `a landed ack put the block past its cap at ${where}`);
    assertWhollyShown(await shownIn(page, "#email", "#go", "#ack"), `${where} after a join`);
    await page.close();
  }
});

// Shorter than the block a print leaves, the window is where the plain cap earns itself: the compact
// block rests inside a 568x170 window with nothing held back — 107.59px of the cap's 130px — and the
// print that arms the ack's strip is what makes the cap bind. Uncapped the block would then hang
// 22.39px off the top of that window; capped it stands 20px inside it. What the cap holds back a hand
// can still roll into view, and what lands in the ack the page rolls in itself.
test("a window shorter than the printed block caps it, and a hand rolls in the rest", async () => {
  const page = await open(6, PHONE_LANDSCAPE_SHORT);
  const { width, height } = page.viewportSize();
  const where = `${width}x${height}`;
  const placed = await placementOf(page);
  assert.ok(placed.top >= 0, `the block's head hangs ${-placed.top}px off the top of the window`);
  assert.ok(
    placed.gap >= BOTTOM_GAP_MIN && placed.gap <= BOTTOM_GAP_MAX,
    `${placed.gap}px between the block and the bottom edge`,
  );
  // Nothing has printed, so the strip reserves nothing and the cap has nothing to hold back: every
  // part of the block, the empty strip included, stands whole on the shortest window driven here.
  assert.equal(placed.scrolled, false, `the cap binds before anything printed at ${where}`);
  assert.equal(placed.reachable, true);
  assertWhollyShown(await shownIn(page, "#email", "#go", "#ack"), where);
  await landAJoin(page, "capped@yourco.com");
  const landed = await placementOf(page);
  assert.equal(landed.scrolled, true, "the cap did not bind once the print armed the strip");
  assert.ok(landed.top >= 0, `the block's head hangs ${-landed.top}px off the top after a join`);
  assert.ok(
    landed.gap >= BOTTOM_GAP_MIN && landed.gap <= BOTTOM_GAP_MAX,
    `${landed.gap}px between the block and the bottom edge after a join`,
  );
  // The strip reserves a line more than a landed ack prints here, so what has to be inside the port
  // is the copy, not the box: the two lines it printed, and both controls with them.
  assertWhollyShown(await shownIn(page, "#email", "#go"), `${where} after a join`);
  assertWhollyShown(await ackCopyShownIn(page), `${where} after a join`);
  // The rest of the strip the cap holds back is what the wheel has to reach, and the print has
  // already spent part of it: standing the two printed lines whole takes 8 of the panel's 42px and
  // leaves the ack's box short of standing whole, so the hand's own scroll is what carries the whole
  // box in — which is why the wheel is measured against where the print left the panel and not
  // against zero.
  const printRolledTo = await page.locator("#join").evaluate((el) => el.scrollTop);
  assert.ok((await rollThePanel(page, 200)) > printRolledTo, "a hand's scroll moved nothing");
  assertWhollyShown(await shownIn(page, "#ack"), `${where} after a hand rolled the panel`);
  await page.close();
});

// Every path that prints in the ack, and what each leaves a member holding. Only a landed join takes
// the entry away: the button is down while a request is in flight, and both refusals hand it back,
// so on three of the four the address is still there to be corrected and on two the button is still
// there to be pressed. Each path drives the page the way the suite's own path tests do.
const REPORT_PATHS = [
  {
    name: "a request in flight",
    opening: "Joining",
    live: ["#email"],
    // A request that never answers, so the ack the submit prints stays up to be measured.
    async drive(page) {
      await page.evaluate(() => (window.fetch = () => new Promise(() => {})));
      await page.fill("#email", "joining@yourco.com");
      await page.click("#go");
    },
  },
  {
    name: "a refused address",
    opening: "That email",
    live: ["#email", "#go"],
    // Native validation passes a dotless domain and the worker's regex does not, so this is a real
    // 400 off the wire — the path a member has to type into again.
    async drive(page) {
      await page.fill("#email", "pilot@localhost");
      await page.click("#go");
    },
  },
  {
    name: "a request that failed",
    opening: "Request failed",
    live: ["#email", "#go"],
    async drive(page) {
      await page.evaluate(() => {
        window.fetch = async () => ({ ok: true, text: () => Promise.reject(new Error("cut")) });
      });
      await page.fill("#email", "cut@off.com");
      await page.click("#go");
    },
  },
  {
    name: "a landed join",
    opening: "#",
    live: [],
    drive: (page, tag) => landAJoin(page, `bound-${tag}@yourco.com`),
  },
];

// The cap binds across a band of window heights rather than at one, and these are three inside it.
// It binds on the report itself: the print arms the ack's strip, which is the room that takes the
// block past every cap in the band. Wherever it binds the ack is the copy held back, and the room
// the page spends to roll it in comes out of the heading: the block's own head keeps standing 20px
// inside the window, and so does every control the path left enabled. Nothing here rests on the
// entry being disabled — three of the four paths leave it live, which is why each is walked.
//
// The port holds both at every height here: 108px of port at 568x150 stands the entry, the button
// beside it and a landed join's two printed lines together.
test("wherever the cap binds, what a report prints reads whole and live controls stay whole", async () => {
  for (const height of [170, 160, 150]) {
    for (const path of REPORT_PATHS) {
      const { viewport } = PHONE_LANDSCAPE_SHORT;
      const page = await open(6, { ...PHONE_LANDSCAPE_SHORT, viewport: { ...viewport, height } });
      const where = `${viewport.width}x${height}, ${path.name}`;
      await path.drive(page, height);
      await page.waitForFunction(
        (opening) => document.getElementById("ack").textContent.startsWith(opening),
        path.opening,
      );
      const reported = await placementOf(page);
      assert.equal(reported.scrolled, true, `the cap does not bind at ${where}`);
      assert.ok(
        reported.top >= 0,
        `the block's head hangs ${-reported.top}px off the top at ${where}`,
      );
      assert.ok(
        reported.gap >= BOTTOM_GAP_MIN && reported.gap <= BOTTOM_GAP_MAX,
        `${reported.gap}px between the block and the bottom edge at ${where}`,
      );
      assertWhollyShown(await ackCopyShownIn(page), where);
      const live = await liveControls(page);
      assert.deepEqual(live, path.live, `${where} left ${live.join(" and ") || "nothing"} enabled`);
      assertWhollyShown(await shownIn(page, ...live), where);
      await page.close();
    }
  }
});

// Two of the four paths hand the button back, so a member can wheel wherever they like and press it
// again — and the ack that prints then lands wherever the panel is standing, not at the head. A
// roll that never moved the panel back would leave that copy read and a control the refusal handed
// back clipped: measured at 568x150, the wheel leaves the panel at its foot 62px down, where #email
// shows 23.59 of its 44px, and the print needs 6 — so the report the resubmit prints is what stands
// the entry whole again. The band is walked because the gap between the wheel's reach and the need
// is the cap's own — 42px against a need of 0 at 170, 62px against 6 at 150.
const REFUSED_ADDRESS = REPORT_PATHS.find((path) => path.name === "a refused address");

test("a report stands its copy and live controls whole from a member's own scroll", async () => {
  for (const height of [170, 160, 150]) {
    const { viewport } = PHONE_LANDSCAPE_SHORT;
    const page = await open(6, { ...PHONE_LANDSCAPE_SHORT, viewport: { ...viewport, height } });
    const where = `${viewport.width}x${height}, a refused address resubmitted from the panel's foot`;
    await REFUSED_ADDRESS.drive(page, `resubmit-${height}`);
    await page.waitForFunction(
      (opening) => document.getElementById("ack").textContent.startsWith(opening),
      REFUSED_ADDRESS.opening,
    );
    const rolled = await page.locator("#join").evaluate((el) => el.scrollTop);
    const theirs = await rollThePanel(page, 400);
    assert.ok(
      theirs > rolled,
      `the wheel did not move the panel past the report's ${rolled} at ${where}`,
    );
    // The button the refusal handed back, pressed from down there: the same address is refused off the
    // wire again, so the button comes back a second time and the wait cannot pass on the first ack.
    await page.click("#go");
    await page.waitForFunction(() => {
      const block = document.getElementById("join");
      return (
        !block.querySelector("#go").disabled &&
        block.querySelector("#ack").textContent.startsWith("That email")
      );
    });
    const stood = await page.locator("#join").evaluate((el) => el.scrollTop);
    assert.ok(stood < theirs, `the report left the panel at the member's ${theirs} at ${where}`);
    assertWhollyShown(await ackCopyShownIn(page), where);
    const live = await liveControls(page);
    assert.deepEqual(
      live,
      REFUSED_ADDRESS.live,
      `${where} left ${live.join(" and ") || "nothing"} enabled`,
    );
    assertWhollyShown(await shownIn(page, ...live), where);
    await page.close();
  }
});

// Room lost after a report is the same clip arriving from the other side, and the window takes it
// two ways. Its bottom edge dragged up walks the port's bottom edge under copy that has already
// printed and changes nothing about the copy: measured on a page that rolled only as it printed, at
// 568x170 → 150 a landed ack stood whole on a scroll of 8 and needs 28 of a port that fell from
// 128px to 108px; at 1200x240 → 180 its 38.39px left the port and the window both, `scrollTop`
// unmoved at 24 and 16. Its side edge dragged in takes the same room without moving the port at
// all: at 568x170 → 320x170 that ack rewraps from two lines onto three, and the copy needs 29 of a
// port that is still 128px, `scrollTop` stuck at the 8 the print left it at. A phone whose URL bar
// comes back, a window edge dragged up and a window edge dragged in, because what changed is the
// room and not the copy.
//
// The room the roll spends is the heading's, as it is when the copy lands: what the resize cannot
// buy back is measured too — at 1200x180 a landed join leaves the disabled entry 23.78 of 35.39px,
// which is why the controls asserted are the ones the path left live. The compact block a coarse
// pointer gets buys the phone legs out of that: 108px of port stands the entry, the button beside
// it and the two lines a join prints together.
const ROOM_LOSING_RESIZES = [
  {
    device: PHONE_LANDSCAPE_SHORT,
    from: { width: 568, height: 170 },
    to: { width: 568, height: 150 },
  },
  {
    device: DESKTOP,
    from: { width: 1200, height: 240 },
    to: { width: 1200, height: 180 },
  },
  {
    device: PHONE_LANDSCAPE_SHORT,
    from: { width: 568, height: 170 },
    to: { width: 320, height: 170 },
  },
];

test("copy that has printed is rolled back in when the window takes its room away", async () => {
  for (const { device, from, to } of ROOM_LOSING_RESIZES) {
    for (const path of REPORT_PATHS) {
      const page = await open(6, { ...device, viewport: from });
      const where = `${from.width}x${from.height} → ${to.width}x${to.height}, ${path.name}`;
      await path.drive(page, `${from.width}x${from.height}-${to.width}x${to.height}`);
      await page.waitForFunction(
        (opening) => document.getElementById("ack").textContent.startsWith(opening),
        path.opening,
      );
      assertWhollyShown(await ackCopyShownIn(page), `${from.width}x${from.height}, ${path.name}`);
      await page.setViewportSize(to);
      await page.waitForFunction(
        ([width, height]) => innerWidth === width && innerHeight === height,
        [to.width, to.height],
      );
      await page.waitForTimeout(120);
      const afterLoss = await placementOf(page);
      assert.equal(afterLoss.scrolled, true, `the cap does not bind at ${where}`);
      assert.ok(
        afterLoss.top >= 0,
        `the block's head hangs ${-afterLoss.top}px off the top at ${where}`,
      );
      assert.ok(
        afterLoss.gap >= BOTTOM_GAP_MIN && afterLoss.gap <= BOTTOM_GAP_MAX,
        `${afterLoss.gap}px between the block and the bottom edge at ${where}`,
      );
      assertWhollyShown(await ackCopyShownIn(page), where);
      assertWhollyShown(await shownIn(page, ...(await liveControls(page))), where);
      await page.close();
    }
  }
});

// A resize is not a loss, and only room lost takes anything from copy that has already printed. A
// window narrowed 568 → 560 at the same height rewraps none of it — the block is 30rem wide at
// both, so not a character moves — and the copy needs the scroll it needed before: 28px on a landed
// join, 6px on the reports that print one line. One grown 150 → 160 hands 10px of port back, which
// is more than a one-line report needed and less than a landed join does, and neither is a loss. In
// neither may the roll throw away a scroll the member made themselves. Both ends are walked on
// each: the move that must not roll, and then a real loss on the same page, which must. 568x150 is
// a height every one of the four paths rolls from, so on each of them the member has somewhere of
// their own to wheel to. Each loss is also taken at a height whose port can hold the printed copy
// and the controls the path left live at once: 140px of window leaves 98px of port against the 72px
// from the entry's top to a one-line report's foot, and the 93.6px to a landed join's.
const ROOM_KEEPING_RESIZES = [
  {
    name: "the window narrows at the same height",
    to: { width: 560, height: 150 },
    shrunkTo: 140,
  },
  {
    name: "the window grows 10px, short of clearing the copy",
    to: { width: 568, height: 160 },
    shrunkTo: 155,
  },
];
const ROOM_KEEPING_FROM = { width: 568, height: 150 };

test("a resize that takes no room away leaves a member's own scroll alone", async () => {
  for (const { name, to, shrunkTo } of ROOM_KEEPING_RESIZES) {
    for (const path of REPORT_PATHS) {
      const page = await open(6, { ...PHONE_LANDSCAPE_SHORT, viewport: ROOM_KEEPING_FROM });
      const where = `${ROOM_KEEPING_FROM.width}x${ROOM_KEEPING_FROM.height}, ${name}, ${path.name}`;
      await path.drive(page, `kept-${to.width}x${to.height}`);
      await page.waitForFunction(
        (opening) => document.getElementById("ack").textContent.startsWith(opening),
        path.opening,
      );
      const rolled = await page.locator("#join").evaluate((el) => el.scrollTop);
      assert.ok(rolled > 0, `the report printed without rolling at ${where}: nothing is at risk`);
      const theirs = await rollThePanel(page, -400);
      assert.ok(theirs < rolled, `the wheel did not move the panel off ${rolled} at ${where}`);
      assertWhollyShown(await shownIn(page, ".head"), `${where}, as the member left it`);
      await page.setViewportSize(to);
      // Both dimensions: one row moves the width alone, and a wait on the height it is already at
      // would resolve before the resize landed and pass a narrowing that never arrived.
      await page.waitForFunction(
        ([width, height]) => innerWidth === width && innerHeight === height,
        [to.width, to.height],
      );
      await page.waitForTimeout(120);
      assert.equal(
        await page.locator("#join").evaluate((el) => el.scrollTop),
        theirs,
        `the resize moved the panel off the member's ${theirs} at ${where}`,
      );
      assertWhollyShown(await shownIn(page, ".head"), `${where}, after the resize`);
      // The gate is a gate and not a stop: room actually lost still rolls the printed copy back in.
      await page.setViewportSize({ width: to.width, height: shrunkTo });
      await page.waitForFunction(
        ([width, height]) => innerWidth === width && innerHeight === height,
        [to.width, shrunkTo],
      );
      await page.waitForTimeout(120);
      const lost = `${where}, then shrunk to ${shrunkTo}`;
      assertWhollyShown(await ackCopyShownIn(page), lost);
      assertWhollyShown(await shownIn(page, ...(await liveControls(page))), lost);
      await page.close();
    }
  }
});

// The other end of the same rule: a print stands the panel at what the copy needs from wherever the
// member left it, and a resize only ever rolls forward from there. Room the window takes away has
// to come from somewhere, but a member already sitting past what the copy needs is reading it, and
// pulling the panel back to the need would throw their own scroll away. At 568x150 → 140 the copy's
// need goes 6 → 16 while the wheel has left them at 62, so the loss is real and it is behind them.
test("a resize rolls the panel forward or not at all", async () => {
  const page = await open(6, { ...PHONE_LANDSCAPE_SHORT, viewport: { width: 568, height: 150 } });
  const where = "568x150 → 568x140, a refused address, the member at the panel's foot";
  await REFUSED_ADDRESS.drive(page, "forward-only");
  await page.waitForFunction(
    (opening) => document.getElementById("ack").textContent.startsWith(opening),
    REFUSED_ADDRESS.opening,
  );
  const rolled = await page.locator("#join").evaluate((el) => el.scrollTop);
  const theirs = await rollThePanel(page, 400);
  assert.ok(
    theirs > rolled,
    `the wheel did not move the panel past the report's ${rolled} at ${where}`,
  );
  await page.setViewportSize({ width: 568, height: 140 });
  await page.waitForFunction(
    ([width, height]) => innerWidth === width && innerHeight === height,
    [568, 140],
  );
  await page.waitForTimeout(120);
  assert.equal(
    await page.locator("#join").evaluate((el) => el.scrollTop),
    theirs,
    `the resize moved the panel off the member's ${theirs} at ${where}`,
  );
  assertWhollyShown(await ackCopyShownIn(page), where);
  await page.close();
});

test("the block fits a phone with nothing clipped", async () => {
  const page = await open(6, PHONE);
  // The longest copy the block ever carries is the ack, so measure with it on screen.
  await landAJoin(page, "phone@yourco.com");
  const laid = await page.locator("#join").evaluate((block) => {
    const box = block.getBoundingClientRect();
    return {
      pageOverflow: document.documentElement.scrollWidth - innerWidth,
      viewport: { width: innerWidth, height: innerHeight },
      left: box.left,
      right: box.right,
      top: box.top,
      bottom: box.bottom,
      clipped: block.scrollWidth > block.clientWidth + 1,
      // nothing clipped means the cap holds nothing back either, not just that the copy fits across
      capped: block.scrollHeight > block.clientHeight + 1,
    };
  });
  assert.equal(laid.pageOverflow, 0);
  assert.equal(laid.clipped, false);
  assert.equal(laid.capped, false);
  assert.ok(
    laid.left >= 0 && laid.right <= laid.viewport.width,
    `the block spans ${laid.left}–${laid.right}px of ${laid.viewport.width}px`,
  );
  assert.ok(
    laid.top >= 0 && laid.bottom <= laid.viewport.height,
    `the block spans ${laid.top}–${laid.bottom}px of ${laid.viewport.height}px`,
  );
  assertWhollyShown(
    await shownIn(page, "#email", "#go", "#ack"),
    `${laid.viewport.width}x${laid.viewport.height} after a join`,
  );
  await page.close();
});

// The bite a keyboard and its accessory bar take out of the VISUAL viewport on that phone. Safari
// leaves the layout viewport at its full height while the visual viewport drops, and it is that
// split that matters, because position:fixed and dvh both resolve against the layout viewport and
// so see no keyboard at all.
const KEYBOARD_PX = 336;
// A keyboard on a phone in landscape, the one case where 5vh of sky and the panel's cap leave less
// room than the block needs.
const LANDSCAPE_KEYBOARD_PX = 230;
// Safari also pans the visual viewport to bring the focused entry into view, so the strip a member
// can see starts this far down the layout viewport while the keyboard is up.
const PANNED_PX = 37;
// The accessory bar on its own, which is the smallest bite a keyboard takes. Over the 568x170
// window it leaves 120px of room, deeper than any of the window-loss legs above reach, so a
// keyboard's answer to printed copy is read where the bite is the smallest one there is.
const ACCESSORY_BAR_PX = 50;

// A keyboard is a shorter visual viewport over an untouched layout viewport, and that is what this
// reports to the page, through the API's own resize event: height and offsetTop overridden, and
// everything else the browser's own — the scale included, which is what the page reads to tell a
// keyboard from a zoom. A keyboard's bite comes out of the screen and a zoom divides what is left of
// it, so the height is derived from the live scale rather than frozen: a pinch held over an open
// keyboard reports what a browser reports, and reports the same thing in either order.
// Installed two ways — into a page already standing, and into one before any of its own script runs
// — so it is written once, at the top level, where it carries no closure to serialise. The layout
// height is read on every get and not frozen, because at the moment an init script runs the window is
// not yet the size the device asked for: 2121px on the phone below, against the 844px it settles at.
function coverWithKeyboard(covers) {
  Object.defineProperty(visualViewport, "height", {
    get: () => (innerHeight - covers) / visualViewport.scale,
    configurable: true,
  });
  Object.defineProperty(visualViewport, "offsetTop", { get: () => 0, configurable: true });
}

async function openKeyboard(page, covers) {
  await page.evaluate(coverWithKeyboard, covers);
  await page.evaluate(() => visualViewport.dispatchEvent(new Event("resize")));
  await page.waitForTimeout(80);
}

// Safari brings a focused entry into view by panning the visual viewport, and the API delivers a pan
// as a scroll and not a resize: the keyboard stays up, its bite stands, and offsetTop alone moves.
async function panTo(page, panned) {
  await page.evaluate((panned) => {
    Object.defineProperty(visualViewport, "offsetTop", { get: () => panned, configurable: true });
    visualViewport.dispatchEvent(new Event("scroll"));
  }, panned);
  await page.waitForTimeout(80);
}

async function closeKeyboard(page) {
  await page.evaluate(() => {
    delete visualViewport.height;
    delete visualViewport.offsetTop;
    visualViewport.dispatchEvent(new Event("resize"));
  });
  await page.waitForTimeout(80);
}

// A pinch is what a page scale factor does emulate, and emulates faithfully: the visual viewport
// shrinks in both axes and the scale rises with it, exactly as under two fingers.
async function pinchTo(page, cdp, scale) {
  await cdp.send("Emulation.setPageScaleFactor", { pageScaleFactor: scale });
  await page.waitForTimeout(300);
}

// The two-finger pan a zoomed-in member has, driven the way the browser drives one: with the page
// itself unscrollable, a wheel pans the visual viewport, and a wheel past the bottom leaves it at
// the furthest pan there is. The API delivers it as its own scroll, which is the event a pan arrives
// on whether it came from a hand or from Safari.
async function panToTheBottom(page) {
  const { width, height } = page.viewportSize();
  await page.mouse.move(width / 2, height / 2);
  await page.mouse.wheel(0, 4000);
  await page.waitForTimeout(300);
}

// The least whole-pixel scroll of the panel that stands one part whole inside its port — the same
// rounding the page's own roll takes, and for the same reason: a need rounded down leaves the part
// its own fraction short, which is where scrollIntoView's rounding lands it.
async function rollIntoThePort(page, selector) {
  await page.locator("#join").evaluate((panel, selector) => {
    const part = panel.querySelector(selector).getBoundingClientRect();
    const clipBottom = panel.getBoundingClientRect().top + panel.clientTop + panel.clientHeight;
    panel.scrollTop = Math.ceil(panel.scrollTop + part.bottom - clipBottom);
  }, selector);
}

// Everything measured against the visual viewport's own edges, which is where a member's eyes are —
// and read off `window` rather than off the bare name, because a browser without the API has no
// binding to read and this suite drives that case.
const visualPlacementOf = (page) =>
  page.locator("#join").evaluate((el) => {
    const viewport = window.visualViewport;
    const block = el.getBoundingClientRect();
    const entry = document.getElementById("email").getBoundingClientRect();
    const button = document.getElementById("go").getBoundingClientRect();
    const top = viewport ? viewport.offsetTop : 0;
    const height = viewport ? viewport.height : innerHeight;
    const scale = viewport ? viewport.scale : 1;
    const middle = document.elementFromPoint(block.x + block.width / 2, block.y + block.height / 2);
    const to2 = (n) => Math.round(n * 100) / 100;
    // how far past a bottom edge each part falls: positive is behind a keyboard, or below the strip
    // a zoom leaves
    const pastEdge = (edge) => ({
      block: to2(block.bottom - edge),
      entry: to2(entry.bottom - edge),
      button: to2(button.bottom - edge),
    });
    return {
      visualHeight: to2(height),
      layoutHeight: innerHeight,
      offsetTop: to2(top),
      scale,
      gap: to2(top + height - block.bottom),
      past: pastEdge(top + height),
      // and the same reading in the layout viewport's own pixels, which is the frame the rects above
      // are measured in and the frame a fixed block is laid out in: the visual height carried back
      // through the scale is the strip a keyboard leaves whatever the zoom
      pastTheKeyboard: pastEdge(top + height * scale),
      clippedAbove: to2(Math.max(0, top - block.top)),
      // the inset as the page wrote it, so a declared 0px and a measured 0.00px are two answers and
      // not one
      insetProp: getComputedStyle(el).getPropertyValue("--keyboard").trim(),
      visibleProp: getComputedStyle(el).getPropertyValue("--visible").trim(),
      keyboardInset: parseFloat(getComputedStyle(el).getPropertyValue("--keyboard")),
      cap: getComputedStyle(el).maxHeight,
      scrolled: el.scrollHeight > el.clientHeight + 1,
      reachable: middle?.closest("#join") === el,
      rolledTo: el.scrollTop,
      box: { y: to2(block.y), height: to2(block.height) },
    };
  });

// An iPhone with the URL bar showing, which is the shortest window a phone is served upright in: the
// block rests there at the compact height and stands at the printed one once a report has armed the
// ack's strip, and what a member sees is what is asserted rather than that arithmetic — the entry and
// the button ride one row, the fleet keeps the greater part of the sky, and nothing is capped behind
// the block's own scroll.
const PHONE_WITH_URL_BAR = { ...PHONE, viewport: { width: 390, height: 664 } };
// The two heights the block has on a coarse pointer: what it rests at with the strip empty, and what
// the strip's three lines take it to on the first print. One figure apiece at every phone width,
// because the entry and the button share a row at all of them.
const PHONE_RESTING_BLOCK_PX = 107.59;
const PHONE_PRINTED_BLOCK_PX = 172.39;
// What the block may take of the window it stands in, resting and printed: those two heights against
// the window, largest upright on the shortest upright window served, and larger again in landscape,
// where the same heights stand over a 390px window.
const PHONE_BLOCK_SHARE_MAX = { resting: 0.17, printed: 0.27 };
const PHONE_NARROW_BLOCK_SHARE_MAX = { resting: 0.2, printed: 0.31 };
const PHONE_LANDSCAPE_BLOCK_SHARE_MAX = { resting: 0.29, printed: 0.45 };

// The third figure is the width the entry is left with, and it is what holds the row honest: the row
// can never wrap — `.field` flexes and the entry's min-width is 0 — so a button that grew would take
// its width out of the entry rather than a line of its own, and one row would still read as one row.
// It is the panel's inner width less the prompt, the row's gap and the button, so 320px is where the
// entry has the least to give.
test("the block a phone gets is compact, and stays compact with the keyboard up", async () => {
  for (const [device, shareMax, entryWidth] of [
    [PHONE_WITH_URL_BAR, PHONE_BLOCK_SHARE_MAX, 206.2],
    [PHONE, PHONE_BLOCK_SHARE_MAX, 206.2],
    [PHONE_LANDSCAPE, PHONE_LANDSCAPE_BLOCK_SHARE_MAX, 327.41],
    [PHONE_NARROW, PHONE_NARROW_BLOCK_SHARE_MAX, 141.8],
  ]) {
    const page = await open(6, device);
    const { width, height } = device.viewport;
    const where = `${width}x${height}`;
    const measure = () =>
      page.locator("#join").evaluate((el) => {
        const block = el.getBoundingClientRect();
        const entry = el.querySelector("#email").getBoundingClientRect();
        const button = el.querySelector("#go").getBoundingClientRect();
        return {
          height: Math.round(block.height * 100) / 100,
          share: Math.round((block.height / innerHeight) * 100) / 100,
          oneRow: Math.round(entry.top) === Math.round(button.top) && button.left >= entry.right,
          entryWidth: Math.round(entry.width * 100) / 100,
        };
      });
    const laid = await measure();
    assert.equal(laid.oneRow, true, `the entry and the button are stacked at ${where}`);
    assert.equal(laid.entryWidth, entryWidth, `the entry takes ${laid.entryWidth}px of ${where}`);
    assert.equal(laid.height, PHONE_RESTING_BLOCK_PX, `the block rests at ${laid.height}px, ${where}`);
    assert.deepEqual(
      await ackRoom(page),
      { lines: 0, reserved: 0 },
      `the strip reserves room before anything printed at ${where}`,
    );
    assert.ok(laid.share <= shareMax.resting, `the block takes ${laid.share} of ${where}`);
    const placed = await visualPlacementOf(page);
    assert.equal(placed.scrolled, false, `the block caps its own copy at ${where}`);
    assertSizedForAFinger(await controls(page));
    // And with the strip armed, which is the tallest the block ever stands: the row is still a row,
    // the copy is inside the port, and nothing is capped behind the block's own scroll.
    await landAJoin(page, `compact-${width}x${height}@yourco.com`);
    const printed = await measure();
    assert.equal(printed.oneRow, true, `a landed join stacked the entry and the button at ${where}`);
    assert.equal(printed.entryWidth, entryWidth, `the entry takes ${printed.entryWidth}px, ${where}`);
    assert.equal(printed.height, PHONE_PRINTED_BLOCK_PX, `the print left ${printed.height}px, ${where}`);
    assert.ok(printed.share <= shareMax.printed, `the printed block takes ${printed.share} of ${where}`);
    assert.equal(
      (await visualPlacementOf(page)).scrolled,
      false,
      `the printed block caps its own copy at ${where}`,
    );
    assertWhollyShown(await ackCopyShownIn(page), `${where} after a join`);
    await page.close();
  }

  // Upright under a keyboard: the block stands whole in the strip the keyboard leaves, at the height
  // it stands at with the keyboard down, and the entry and the button stand in there with it. Read
  // with a join landed, because the armed strip is the block the keyboard has the least room for.
  const page = await open(6, PHONE_WITH_URL_BAR);
  await landAJoin(page, "keyboard-compact@yourco.com");
  const resting = await visualPlacementOf(page);
  assert.equal(resting.box.height, PHONE_PRINTED_BLOCK_PX);
  await openKeyboard(page, KEYBOARD_PX);
  const lifted = await visualPlacementOf(page);
  const where = `390x664 under a ${KEYBOARD_PX}px keyboard`;
  assert.equal(lifted.keyboardInset, KEYBOARD_PX, where);
  assert.equal(lifted.box.height, resting.box.height, `the keyboard shrank the block, ${where}`);
  assert.equal(lifted.scrolled, false, `the cap holds the block's copy back at ${where}`);
  assert.equal(lifted.clippedAbove, 0, `the block's head clips ${lifted.clippedAbove}px, ${where}`);
  assert.ok(
    lifted.gap >= BOTTOM_GAP_MIN && lifted.gap <= BOTTOM_GAP_MAX,
    `${lifted.gap}px between the block and the keyboard`,
  );
  assertWhollyShown(await shownIn(page, "#email", "#go", "#ack"), where);
  assertWhollyShown(await ackCopyShownIn(page), where);
  await page.close();
});

test("the block rides above an open keyboard instead of waiting behind it", async () => {
  const page = await open(6, PHONE);
  const resting = await visualPlacementOf(page);
  assert.equal(resting.visualHeight, resting.layoutHeight);
  assert.equal(resting.keyboardInset, 0);

  await openKeyboard(page, KEYBOARD_PX);
  const lifted = await visualPlacementOf(page);
  assert.equal(lifted.layoutHeight, PHONE.viewport.height); // the layout viewport never noticed
  assert.equal(lifted.scale, 1); // and neither did the page scale: a keyboard, not a pinch
  assert.equal(lifted.keyboardInset, KEYBOARD_PX);
  for (const [part, past] of Object.entries(lifted.past)) {
    assert.ok(past < 0, `the ${part} sits ${past}px past the visual viewport's bottom edge`);
  }
  assert.equal(lifted.clippedAbove, 0);
  assert.equal(lifted.reachable, true);
  assert.ok(
    lifted.gap >= BOTTOM_GAP_MIN && lifted.gap <= BOTTOM_GAP_MAX,
    `${lifted.gap}px between the block and the keyboard`,
  );
  // The cap is the height still visible less that sky top and bottom, so the block keeps the whole
  // of itself inside what the member can see: 423.6px of the 508px the keyboard leaves.
  assert.ok(
    Math.abs(parseFloat(lifted.cap) - (lifted.visualHeight - 2 * lifted.gap)) < 0.05,
    `the panel caps at ${lifted.cap} in ${lifted.visualHeight}px of visible viewport`,
  );
  assert.equal(lifted.scrolled, false);
  // Lifted by the keyboard's height and no further, so the sky below the block is the same sky.
  const lift = resting.box.y - lifted.box.y;
  assert.ok(
    Math.abs(lift - KEYBOARD_PX) < 0.5,
    `the block rose ${lift}px for a ${KEYBOARD_PX}px bite`,
  );
  assert.equal(lifted.box.height, resting.box.height);
  assert.equal(await page.locator("#join").isVisible(), true);

  // and settles back onto the foot of the page when the keyboard closes
  await closeKeyboard(page);
  assert.deepEqual(await visualPlacementOf(page).then((p) => p.box), resting.box);
  await page.close();
});

// A page can load with the keyboard already up — a reload from a focused entry, or a tab coming back
// to one — and no resize announces what is already true, so the measurement the page takes on the way
// in is the only one it gets. The bite is installed before any of the page's own script runs and no
// event is dispatched, so what stands here is that first reading.
test("a page that loads with the keyboard already up is lifted on the way in", async () => {
  const page = await open(6, PHONE, coverWithKeyboard, KEYBOARD_PX);
  const lifted = await visualPlacementOf(page);
  assert.equal(lifted.layoutHeight, PHONE.viewport.height);
  assert.equal(lifted.keyboardInset, KEYBOARD_PX);
  assert.equal(lifted.clippedAbove, 0);
  assert.ok(
    lifted.gap >= BOTTOM_GAP_MIN && lifted.gap <= BOTTOM_GAP_MAX,
    `${lifted.gap}px between the block and the keyboard`,
  );
  for (const [part, past] of Object.entries(lifted.past)) {
    assert.ok(past < 0, `the ${part} sits ${past}px past the visual viewport's bottom edge`);
  }

  // Again with every resize taken away, on the API and on the window alike, because Chromium resizes
  // both once on the way up — the window arrives at 2121px and settles at 844px — and a page that
  // only ever answered an event is carried by that one. Deaf to both, the reading the page takes on
  // the way in is the only reading there is.
  await page.addInitScript(() => {
    visualViewport.addEventListener = () => {};
    const listen = window.addEventListener.bind(window);
    window.addEventListener = (type, ...rest) => {
      if (type !== "resize") listen(type, ...rest);
    };
  });
  await page.reload();
  await page.waitForFunction(() => document.querySelector(".craft")?.style.opacity === "1");
  const deaf = await visualPlacementOf(page);
  assert.equal(deaf.keyboardInset, KEYBOARD_PX);
  assert.deepEqual(deaf.box, lifted.box);
  await page.close();
});

// A keyboard leaves 160px of a phone in landscape, and no arrangement of the 172.39px block a print
// leaves fits in that, so this is the one case where the cap binds and the panel scrolls its own
// copy. A join is landed first for that reason: the compact 107.59px the block rests at stands whole
// in the 120px the cap allows there, and the strip the print arms is what the cap has to hold back.
// The cap has to be the height a member can see: taken from the layout height less the keyboard it
// overshoots by the pan, and the pan is driven the way Safari delivers one, as the API's own scroll.
// Panned or not, the block's foot stands 20px above the strip's bottom edge and its head 20px inside
// the top: the inset is measured to that bottom edge, which the pan moves, and a fixed block is
// painted with the pan, so it comes up 37px with it rather than clipping 37px off the top.
test("the panel's cap is the height a member can see, panned or not", async () => {
  const page = await open(6, PHONE_LANDSCAPE);
  const tall = PHONE_LANDSCAPE.viewport.height;
  const visible = tall - LANDSCAPE_KEYBOARD_PX;
  assert.equal((await visualPlacementOf(page)).cap, `${tall - 2 * BOTTOM_GAP_MIN}px`);
  await landAJoin(page, "landscape-keyboard@yourco.com");
  await openKeyboard(page, LANDSCAPE_KEYBOARD_PX);

  for (const panned of [0, PANNED_PX]) {
    // Where a keyboard opening on an untouched panel leaves it, which is what the strip has to hold
    // whole, and where the hand below starts from.
    await page.locator("#join").evaluate((el) => (el.scrollTop = 0));
    await panTo(page, panned);
    const under = await visualPlacementOf(page);
    const where = `panned ${panned}px`;
    assert.equal(under.layoutHeight, tall, where);
    assert.equal(under.scale, 1, where);
    assert.equal(under.keyboardInset, LANDSCAPE_KEYBOARD_PX - panned, where);
    assert.equal(under.cap, `${visible - 2 * BOTTOM_GAP_MIN}px`, where);
    assert.equal(under.box.height, visible - 2 * BOTTOM_GAP_MIN, where);
    assert.equal(under.past.block, -BOTTOM_GAP_MIN, where);
    assert.equal(under.reachable, true, where);
    assert.equal(
      under.clippedAbove,
      0,
      `the panel's head clips ${under.clippedAbove}px off the visible strip, ${where}`,
    );

    // Bound, the panel scrolls: the entry stands whole where the keyboard left the panel, and a
    // hand's own wheel brings the button and the ack in after it. Every part is measured against the
    // panel's port and the strip the keyboard leaves, which is what shownIn's two bounds are.
    assert.equal(under.scrolled, true, where);
    assertWhollyShown(await shownIn(page, "#email"), `${where}, where the keyboard left the panel`);
    assert.ok((await rollThePanel(page, 400)) > 0, `a hand's scroll moved nothing, ${where}`);
    for (const part of ["#email", "#go", "#ack"]) {
      await rollIntoThePort(page, part);
      assertWhollyShown(await shownIn(page, part), `${part} rolled into the port, ${where}`);
    }
  }

  await closeKeyboard(page);
  await page.close();
});

// A pinch shrinks the same viewport a keyboard does, and nothing about it is a keyboard: nothing is
// covered, so the block stays at the foot of the page and the strip a zoom leaves is a window onto
// the page that the member's own two fingers move. The pan a zoomed-in member already has is what
// reaches it: wheeled to the furthest pan the block's foot stands in the strip with the same 5vh of
// sky under it, at every scale here.
//
// A phone in landscape leads, at the scale that leaves the block's head the least room above it:
// 73.61px of layout viewport there, against 525.42px on the phone upright.
test("a pinch zoom lifts the block not at all, and a pan brings it back", async () => {
  for (const [device, scales] of [
    [PHONE_LANDSCAPE, [1.5]],
    [PHONE, [2, 3]],
    [DESKTOP, [2.5]],
  ]) {
    const page = await open(6, device);
    const cdp = await page.context().newCDPSession(page);
    const resting = await visualPlacementOf(page);
    for (const scale of scales) {
      await pinchTo(page, cdp, scale);
      const zoomed = await visualPlacementOf(page);
      const where = `at ${scale}x on ${device.viewport.width}x${device.viewport.height}`;
      // a real pinch: the scale rose, and the visual viewport shrank with it
      assert.ok(zoomed.scale > 1, `the scale stayed ${zoomed.scale} ${where}`);
      assert.ok(zoomed.visualHeight < resting.visualHeight, `${zoomed.visualHeight}px ${where}`);
      // the shrink is not a bite, so nothing is lifted and nothing is capped
      assert.equal(zoomed.keyboardInset, 0, `the zoom's shrink was taken as a keyboard ${where}`);
      assert.deepEqual(zoomed.box, resting.box, `the block moved ${where}`);
      assert.equal(zoomed.cap, resting.cap, `the cap bound ${where}`);
      assert.equal(zoomed.scrolled, false, `the panel scrolled its own copy ${where}`);
      assert.equal(
        zoomed.clippedAbove,
        0,
        `the panel's head clips ${zoomed.clippedAbove}px off the visible strip ${where}`,
      );
      // The block is at the foot of a page the strip no longer reaches, and the pan reaches it.
      assert.ok(zoomed.past.block > 0, `the block stands inside the strip unpanned ${where}`);
      await panToTheBottom(page);
      const panned = await visualPlacementOf(page);
      assert.ok(panned.offsetTop > 0, `the pan moved nothing ${where}`);
      assert.equal(panned.keyboardInset, 0, `the pan was taken as a keyboard ${where}`);
      assert.deepEqual(panned.box, resting.box, `the pan moved the block ${where}`);
      for (const [part, past] of Object.entries(panned.past)) {
        assert.ok(past < 0, `the ${part} is still ${past}px past the strip, panned ${where}`);
      }
      // The block's own sky, less what the furthest pan rounds away: Chromium clamps that pan to a
      // whole pixel of the device's own, which leaves up to a pixel less strip under the block than
      // the page puts there — 41.52px of a resting 42.19px at 3x. Where the block stands is the
      // deepEqual above; this is the strip's edge, so it is bounded by the band the suite carries.
      assert.ok(
        panned.gap >= BOTTOM_GAP_MIN && panned.gap <= BOTTOM_GAP_MAX,
        `${panned.gap}px of sky under the block, panned ${where}`,
      );
    }
    await pinchTo(page, cdp, 1);
    assert.deepEqual(await visualPlacementOf(page).then((p) => p.box), resting.box);
    await page.close();
  }
});

// The two are held at once whenever a member zooms in with the keyboard up, and a keyboard's bite
// is still exactly its own bite under a zoom: the visible height carried back through the scale is
// the screen's own, so the zoom divides both sides of the subtraction and cancels out of it. Either
// order has to end in the same place, because the state is the same state whichever event arrived
// last, and both have to end where the keyboard alone put the block — the same box, the same cap,
// and the same 5vh of sky between its foot and the strip the keyboard covers. The cap answers the
// keyboard whatever the scale, so the block's head stands on the strip and the panel's own scroll
// reaches the copy below it; what the zoom leaves visible from there is the member's own pan.
//
// Both devices, because the cap binds on one of them: 336px of keyboard on the phone leaves the
// 172.39px block a print leaves room to stand whole, and 230px in landscape leaves 160px for that
// same block, so there the cap holds copy back and the panel scrolls it. 1.011x is the first scale
// above the deadband, where the cap is handed back with nothing covered, and 3x the deepest the suite
// drives.
const HELD_PINCHES = [
  { device: PHONE, covers: KEYBOARD_PX },
  { device: PHONE_LANDSCAPE, covers: LANDSCAPE_KEYBOARD_PX },
];

// A join is landed on every page here, which is what makes the landscape leg the bound case: the
// ack's strip reserves nothing until a report prints, and the 107.59px the block rests at stands
// whole in the 120px that keyboard's cap allows.
test("a pinch held over an open keyboard keeps the block off it, either order", async () => {
  for (const { device, covers } of HELD_PINCHES) {
    const on = `${device.viewport.width}x${device.viewport.height} under a ${covers}px keyboard`;
    const upright = await open(6, device);
    await landAJoin(upright, `held-upright-${covers}@yourco.com`);
    const resting = await visualPlacementOf(upright);
    await openKeyboard(upright, covers);
    const keyboardAlone = await visualPlacementOf(upright);
    await upright.close();

    const held = {};
    for (const scale of [1.011, 3]) {
      held[scale] = {};
      for (const order of ["keyboard first", "pinch first"]) {
        const page = await open(6, device);
        const cdp = await page.context().newCDPSession(page);
        await landAJoin(page, `held-${covers}-${scale}-${order.split(" ")[0]}@yourco.com`);
        if (order === "keyboard first") {
          await openKeyboard(page, covers);
          await pinchTo(page, cdp, scale);
        } else {
          await pinchTo(page, cdp, scale);
          await openKeyboard(page, covers);
        }
        const both = await visualPlacementOf(page);
        const where = `${order}, at ${scale}x, ${on}`;
        assert.ok(both.scale > 1, `the scale stayed ${both.scale}, ${where}`);
        // the zoom is out of the bite, and the lift is the keyboard's own either way
        assert.equal(both.keyboardInset, covers, where);
        assert.deepEqual(both.box, keyboardAlone.box, `the zoom moved the block, ${where}`);
        // the sky between the block's foot and the strip the keyboard covers, in the layout
        // viewport's own pixels: the frame the block is laid out in, which is where its own 5vh is
        const clearOfTheKeyboard = -both.pastTheKeyboard.block;
        assert.ok(
          Math.abs(clearOfTheKeyboard - resting.gap) < 0.05,
          `${clearOfTheKeyboard}px between the block and the strip the keyboard covers, ${where}`,
        );
        // and the cap is the keyboard's own, so the head stands on the strip a member sees
        assert.equal(both.cap, keyboardAlone.cap, `the cap left the keyboard's own, ${where}`);
        assert.equal(both.visibleProp, keyboardAlone.visibleProp, where);
        assert.equal(
          both.clippedAbove,
          0,
          `the panel's head clips ${both.clippedAbove}px off the visible strip, ${where}`,
        );
        assert.equal(both.reachable, true, `nothing a finger lands on is the block, ${where}`);
        assert.equal(both.scrolled, keyboardAlone.scrolled, where);
        held[scale][order] = both;
        await page.close();
      }
      assert.deepEqual(
        held[scale]["pinch first"],
        held[scale]["keyboard first"],
        `the order the two arrived in changed the block at ${scale}x, ${on}`,
      );
    }
  }
});

// The slack above 1 the page carries as its deadband: Safari reports a scale a hair over 1 of its
// own accord, and a hair is covered-and-not-zoomed. Inside it the cap is still the strip the keyboard
// leaves, taken in the layout viewport's own pixels — the same reading taken in the visual
// viewport's stands 418.57px against the 423.6px the same keyboard leaves at 1x, holding back copy
// that fits. With nothing covered the deadband is the whole decision: inside it the page keeps
// measuring the window, and above it the declared 100dvh is handed back to the stylesheet.
const IN_THE_DEADBAND = 1.01;
const ZOOMED = 1.011;

test("a scale a hair above 1 is slack and not a zoom", async () => {
  for (const { device, covers } of HELD_PINCHES) {
    const page = await open(6, device);
    const cdp = await page.context().newCDPSession(page);
    await openKeyboard(page, covers);
    const alone = await visualPlacementOf(page);
    await pinchTo(page, cdp, IN_THE_DEADBAND);
    const hair = await visualPlacementOf(page);
    const { width, height } = device.viewport;
    const where = `a ${covers}px keyboard at ${IN_THE_DEADBAND}x on ${width}x${height}`;
    assert.ok(hair.scale > 1, `the scale stayed ${hair.scale}, ${where}`);
    assert.equal(hair.keyboardInset, covers, where);
    assert.equal(hair.cap, alone.cap, `the cap left the keyboard's own ${alone.cap}, ${where}`);
    assert.equal(hair.visibleProp, alone.visibleProp, where);
    assert.deepEqual(hair.box, alone.box, `the hair of zoom moved the block, ${where}`);
    assert.equal(
      hair.clippedAbove,
      0,
      `the panel's head clips ${hair.clippedAbove}px off the visible strip, ${where}`,
    );
    assert.equal(hair.reachable, true, `nothing a finger lands on is the block, ${where}`);
    assert.ok(hair.past.block < 0, `the block sits ${hair.past.block}px past the strip, ${where}`);
    assert.ok(hair.pastTheKeyboard.block < 0, `${hair.pastTheKeyboard.block}px, ${where}`);
    await page.close();
  }

  // Nothing covered, where the deadband alone decides: the same window, read as slack and then as a
  // zoom. The cap it measures there is the window's own, which is what the declared 100dvh resolves
  // to, so what the two answers differ in is which of them wrote it.
  const page = await open(6, PHONE);
  const cdp = await page.context().newCDPSession(page);
  const resting = await visualPlacementOf(page);
  assert.equal(resting.visibleProp, `${PHONE.viewport.height}.00px`);
  for (const [scale, visibleProp] of [
    [IN_THE_DEADBAND, resting.visibleProp],
    [ZOOMED, "100dvh"],
  ]) {
    await pinchTo(page, cdp, scale);
    const zoomed = await visualPlacementOf(page);
    const where = `at ${scale}x with nothing covered`;
    assert.equal(zoomed.keyboardInset, 0, `the zoom's shrink was taken as a keyboard ${where}`);
    assert.equal(zoomed.visibleProp, visibleProp, where);
    assert.equal(zoomed.cap, resting.cap, `the cap bound ${where}`);
    assert.deepEqual(zoomed.box, resting.box, `the block moved ${where}`);
  }
  await page.close();
});

// A browser without the API has no binding at all, which is what deleting it leaves. The page keeps
// the declared 0px and 100dvh, so it keeps the plain bottom anchoring — the inset is read as the
// string the stylesheet declares, which a page that measured a keyboard writes 0.00px over.
test("a browser with no visual viewport keeps the plain bottom anchoring", async () => {
  const page = await open(6, PHONE, () => {
    delete window.visualViewport;
  });
  const cdp = await page.context().newCDPSession(page);
  assert.equal(await page.evaluate(() => "visualViewport" in window), false);
  const resting = await visualPlacementOf(page);
  assert.equal(resting.insetProp, "0px");
  assert.equal(resting.visibleProp, "100dvh");
  assert.ok(
    resting.gap >= BOTTOM_GAP_MIN && resting.gap <= BOTTOM_GAP_MAX,
    `${resting.gap}px between the block and the bottom edge`,
  );
  // Without the API there is nothing to measure a keyboard with, so the block keeps the plain
  // bottom anchoring: a pinch, the one viewport change drivable without the API, moves it not at
  // all, and nothing is thrown on the way.
  await pinchTo(page, cdp, PHONE.viewport.height / (PHONE.viewport.height - KEYBOARD_PX));
  const shrunk = await visualPlacementOf(page);
  assert.deepEqual(shrunk.box, resting.box);
  assert.equal(shrunk.insetProp, "0px");
  assert.equal(shrunk.cap, resting.cap);
  await page.close();
});

// The cap and the copy inside it answer to the same scarce room, and a keyboard takes that room the
// way a window edge dragged up does — the port's bottom edge walks up past copy that has already
// printed — except that the window fires no resize for it. Measured with a landed join standing
// whole: an accessory bar's 50px off 568x170 leaves 120px of window, and the copy that stood whole
// on a scroll of 8 needs 58 of the 78px of port that leaves; a keyboard on a phone in landscape
// leaves 118px of port and takes the same copy from a scroll of 0 to one of 18. So the roll the
// resize owns is the roll this runs, at the one point where the page moves the port's height
// itself.
//
// The full keyboard is walked on a landed join alone, because it is the deepest bite the suite
// drives and a join's is the longest copy the block prints: what a shorter report leaves standing
// under a keyboard the accessory bar's own legs read on all four paths.
const LANDED_JOIN = REPORT_PATHS.find((path) => path.name === "a landed join");
const ROOM_LOSING_KEYBOARDS = [
  { device: PHONE_LANDSCAPE_SHORT, covers: ACCESSORY_BAR_PX, paths: REPORT_PATHS },
  { device: PHONE_LANDSCAPE, covers: LANDSCAPE_KEYBOARD_PX, paths: [LANDED_JOIN] },
];

test("copy that has printed is rolled back in when a keyboard takes its room away", async () => {
  for (const { device, covers, paths } of ROOM_LOSING_KEYBOARDS) {
    for (const path of paths) {
      const page = await open(6, device);
      const { width, height } = device.viewport;
      const where = `${width}x${height} under a ${covers}px keyboard, ${path.name}`;
      await path.drive(page, `keyboard-${covers}`);
      await page.waitForFunction(
        (opening) => document.getElementById("ack").textContent.startsWith(opening),
        path.opening,
      );
      assertWhollyShown(await ackCopyShownIn(page), `${width}x${height}, ${path.name}`);
      await openKeyboard(page, covers);
      const under = await visualPlacementOf(page);
      assert.equal(under.keyboardInset, covers, where);
      assert.equal(under.scrolled, true, `the cap does not bind at ${where}`);
      assert.equal(
        under.clippedAbove,
        0,
        `the panel's head clips ${under.clippedAbove}px off the visible strip at ${where}`,
      );
      assert.ok(
        under.gap >= BOTTOM_GAP_MIN && under.gap <= BOTTOM_GAP_MAX,
        `${under.gap}px between the block and the keyboard at ${where}`,
      );
      assertWhollyShown(await ackCopyShownIn(page), where);
      assertWhollyShown(await shownIn(page, ...(await liveControls(page))), where);
      await page.close();
    }
  }
});

// The keyboard's roll carries the resize's own gate with it, both ends of it. A keyboard closing
// hands the room back and rolls nothing, so the head a member wheeled up to read stays where they
// left it; the same keyboard opening again is room lost again, and rolls. At 568x170 the copy needs
// 8px with the keyboard down and 58px with an accessory bar up, and the member is at 0.
test("a keyboard that hands the room back leaves a member's own scroll alone", async () => {
  const page = await open(6, PHONE_LANDSCAPE_SHORT);
  await landAJoin(page, "keyboard-keeps@yourco.com");
  await openKeyboard(page, ACCESSORY_BAR_PX);
  const rolled = (await visualPlacementOf(page)).rolledTo;
  assert.ok(rolled > 0, "the keyboard took no room to roll: nothing is at risk");
  const theirs = await rollThePanel(page, -400);
  assert.ok(theirs < rolled, `the wheel did not move the panel off ${rolled}`);
  assertWhollyShown(await shownIn(page, ".head"), "as the member left it");

  await closeKeyboard(page);
  assert.equal(
    (await visualPlacementOf(page)).rolledTo,
    theirs,
    `the keyboard closing moved the panel off the member's ${theirs}`,
  );
  assertWhollyShown(await shownIn(page, ".head"), "after the keyboard closed");

  await openKeyboard(page, ACCESSORY_BAR_PX);
  assert.equal(
    (await visualPlacementOf(page)).rolledTo,
    rolled,
    "the keyboard opening again took room away and rolled nothing back in",
  );
  assertWhollyShown(await ackCopyShownIn(page), "the keyboard up again");
  await page.close();
});

// The window's own resize arrives before the visual viewport's, so the cap the roll measures
// printed copy against is the one the window's resize writes itself. What it left behind at that
// moment is read from a listener registered after the page's own: at 568x170 → 150 the port is the
// new window's 108px and the copy that needs 28px of scroll is already rolled in, where the window
// it just left stood 128px of port and 8px of scroll.
test("a window resize caps the block to the window it lands in before the roll reads it", async () => {
  const page = await open(6, PHONE_LANDSCAPE_SHORT);
  await landAJoin(page, "resize-cap@yourco.com");
  await page.evaluate(() => {
    window.__atTheResize = [];
    addEventListener("resize", () => {
      const el = document.getElementById("join");
      window.__atTheResize.push({
        innerHeight,
        visibleProp: getComputedStyle(el).getPropertyValue("--visible").trim(),
        cap: getComputedStyle(el).maxHeight,
        clientHeight: el.clientHeight,
        rolledTo: el.scrollTop,
      });
    });
  });
  await page.setViewportSize({ width: 568, height: 150 });
  await page.waitForFunction(() => innerHeight === 150);
  await page.waitForTimeout(120);
  assert.deepEqual(
    await page.evaluate(() => window.__atTheResize),
    [{ innerHeight: 150, visibleProp: "150.00px", cap: "110px", clientHeight: 108, rolledTo: 28 }],
  );
  assertWhollyShown(await ackCopyShownIn(page), "568x170 → 568x150");
  await page.close();
});

// The path a browser without the API keeps: nothing there measures a keyboard, the declared 100dvh
// follows the window itself, and the window's own resize is the only thing that answers copy the
// window has taken the room from. At 568x170 → 150 the ack's printed copy needs 28px of the panel's
// own scroll where it needed 8.
test("a browser with no visual viewport rolls printed copy back in when the window shrinks", async () => {
  const page = await open(6, PHONE_LANDSCAPE_SHORT, () => {
    delete window.visualViewport;
  });
  assert.equal(await page.evaluate(() => "visualViewport" in window), false);
  await landAJoin(page, "no-viewport-resize@yourco.com");
  const printed = await page.locator("#join").evaluate((el) => el.scrollTop);
  await page.setViewportSize({ width: 568, height: 150 });
  await page.waitForFunction(() => innerHeight === 150);
  await page.waitForTimeout(120);
  const where = "568x170 → 568x150 with no visual viewport";
  const rolled = await page.locator("#join").evaluate((el) => el.scrollTop);
  assert.ok(rolled > printed, `the panel stands at ${rolled} where the print left it at ${printed}`);
  assertWhollyShown(await ackCopyShownIn(page), where);
  assertWhollyShown(await shownIn(page, ...(await liveControls(page))), where);
  await page.close();
});

// 5vh of sky under the block, and both ends of the clamp that holds it: a window tall enough for 5vh
// to want 60px keeps the ceiling's 48, one short enough for it to want 12.5px keeps the floor's 20,
// and the two in between keep the 5vh they measure. The two ends are stated as the constants the
// placement assertions bound with, so a clamp and the numbers the suite carries for it cannot drift
// apart in either direction.
test("the sky under the block is 5vh, clamped at both ends", async () => {
  for (const [device, gap] of [
    [DESKTOP_TALL, BOTTOM_GAP_MAX],
    [DESKTOP, 40],
    [DESKTOP_SHORT, 30],
    [PHONE_LANDSCAPE_SHORT, BOTTOM_GAP_MIN],
  ]) {
    const page = await open(6, device);
    const placed = await page.locator("#join").evaluate((el) => {
      const b = el.getBoundingClientRect();
      const to2 = (n) => Math.round(n * 100) / 100;
      return {
        gap: to2(innerHeight - b.bottom),
        offCentreX: to2(Math.abs(b.x + b.width / 2 - innerWidth / 2)),
        keyboardInset: parseFloat(getComputedStyle(el).getPropertyValue("--keyboard")),
      };
    });
    assert.deepEqual(placed, { gap, offCentreX: 0, keyboardInset: 0 });
    await page.close();
  }
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

// Both glass facts are facts about luminance, so they are read off real pixels in linear light, in
// the strip the ack reserves — which prints no copy until a join lands, so what changes there is
// sky. What is read there is the tint and not whichever morph the clock happened to land on: craft
// #0 is rewound to its own seed, walked to a numbered leg of its own tour, and held there with the
// morph in flight dropped and one frame of that leg queued in its place, at both shimmer phases and
// on whole pixels. Held that way a state repeats to the digit inside a page, and within 4% of
// itself across processes.
const GLASS_LEGS = 24;           // craft #0's first 24 forms of its own tour
const GLASS_PHASES = [0, 1];
// The floor is a fact about the tint, so it is measured against a tint that swallows the craft
// rather than against a fixed luminance: over one held craft, the shipped pane must pass three times
// the light an rgba(0,0,0,.9) pane passes there. Over the 48 states below the shipped pane passes
// 4.7× to 11.0×, and the lighter pane the self-test ships 1.4× to 1.9×.
//
// A fixed luminance cannot carry this fact. What the pane passes is the craft's own brightness as
// much as the glass's: 0.00600 through a bell on leg 20, 0.05861 through a tic-tac on leg 3 — and the
// dimmest of those is 13% above the 0.00531 the .9 pane passes on its own brightest leg, so any fixed
// number lands inside one range or the other.
const CRAFT_THROUGH_GLASS_MIN = 3;
// The ceiling is absolute, because legibility is: hull — the panel's own colour, read off the page
// and turned into linear light here — must hold this much against the brightest pixel the pane
// passes. Over the 48 states it holds 3.09:1 (that tic-tac) to 6.00:1, and the gate sits a fifteenth
// under the worst of them because the reading moves up to 4% between processes. It is the
// tint's own gate either way: lighten the pane to .55 and the tic-tac's light holds 2.71:1, to .5
// and 2.38:1, and with the backdrop filter gone 2.30:1. The black halo on every line of the block
// is what carries the copy the rest of the way.
const COPY_CONTRAST_MIN = 2.9;
// The strip with the fleet held away from it, which measured 0 in every reading taken — stated as a
// gate of its own, because it is what makes the reading above the craft and nothing else.
const GLASS_UNLIT_MAX = 0.0005;
// The pane's own declarations, pinned: the tint, the filter as Chromium computes it and as the
// stylesheet declares it, and the halo each line of the block prints.
const TINT_ALPHA = 0.6;
const BACKDROP_COMPUTED = "blur(3px) saturate(1.8)";
const BACKDROP_DECLARED = "blur(3px) saturate(180%)";
const HALO = "rgb(0, 0, 0) 0px 0px 3px, rgb(0, 0, 0) 0px 0px 6px, rgb(0, 0, 0) 0px 0px 12px";
const ACK_HALO =
  "rgb(0, 0, 0) 0px 0px 3px, rgb(0, 0, 0) 0px 0px 6px, rgba(157, 255, 176, 0.333) 0px 0px 10px";
const ACK_BAD_HALO = "rgb(0, 0, 0) 0px 0px 3px, rgb(0, 0, 0) 0px 0px 6px";
// The pane the floor measures against, and a lighter pane the floor must turn down all the same.
const SWALLOWING_TINT = "rgba(0, 0, 0, 0.9)";
const TURNED_DOWN_TINT = "rgba(0, 0, 0, 0.85)";

// The WCAG relative luminance of a computed colour, and the ratio two of them hold.
function linearLuminance(colour) {
  const [r, g, b] = colour
    .match(/\d+(\.\d+)?/g)
    .slice(0, 3)
    .map((channel) => Number(channel) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
const contrast = (copy, behind) => (copy + 0.05) / (behind + 0.05);

// One frame to take a change and one to filter it: the pane re-samples its backdrop a frame behind
// the paint, so a shot taken without this reads the frame before the one under test.
const settle = (page) =>
  page.evaluate(
    () => new Promise((painted) => requestAnimationFrame(() => requestAnimationFrame(painted))),
  );

// The brightest pixel of one region of the screen, in linear light. Clipped to the region, so what
// is decoded is the strip itself.
async function peakUnder(page, box) {
  const shot = (await page.screenshot({ clip: box })).toString("base64");
  return page.evaluate(async (shot) => {
    const image = new Image();
    image.src = `data:image/png;base64,${shot}`;
    await image.decode();
    const canvas = document.createElement("canvas");
    canvas.width = image.width;
    canvas.height = image.height;
    const context = canvas.getContext("2d");
    context.drawImage(image, 0, 0);
    const pixels = context.getImageData(0, 0, image.width, image.height).data;
    const linear = (c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
    let peak = 0;
    for (let i = 0; i < pixels.length; i += 4) {
      peak = Math.max(
        peak,
        0.2126 * linear(pixels[i] / 255) +
          0.7152 * linear(pixels[i + 1] / 255) +
          0.0722 * linear(pixels[i + 2] / 255),
      );
    }
    return peak;
  }, shot);
}

// Park the fleet down the left margin, further apart than the formation radius so nothing pushes
// anything, and hold craft #0 on one leg of its own seeded tour over `box`. The page's own renderer
// draws it and the page's own loop keeps drawing it: the morph in flight is dropped and that leg's
// single frame is queued in its place, so the pixels stop changing without the clock being touched.
// `box` null parks craft #0 with the rest, which is the reading with nothing behind the strip.
const PARK_STRIDE = 150;
const HELD_FRAMES = 400;

async function holdCraftOnLeg(page, box, leg, shimmer) {
  const held = await page.evaluate(
    ({ target, leg, shimmer, stride, frames }) => {
      fleet.forEach((craft, i) => {
        craft.queue.length = 0;
        craft.holdTimer = Infinity;   // no further leg starts
        craft.timer = Infinity;       // and the cloak cycle never comes round
        craft.phase = "visible";
        craft.opacity = 1;
        craft.vx = 0;
        craft.vy = 0;
        craft.x = 4;
        craft.y = 4 + i * stride;
      });
      const craft = fleet[0];
      craft.rng = mulberry32(SEED_STRIDE >>> 0);   // craft #0's own seed, ((0+1)*stride), rewound
      craft.current = randomCraft(craft.rng);
      for (let k = 0; k < leg; k++) nextLeg(craft);
      const pinned = { ...craft.current, phase: (craft.current.phase + shimmer) & 1 };
      const grid = renderGrid(pinned);
      craft.el.innerHTML = gridToHTML(grid);
      for (let k = 0; k < frames; k++) craft.queue.push(grid);
      const drawn = craft.el.getBoundingClientRect();
      craft.w = drawn.width;
      craft.h = drawn.height;
      if (target) {
        // Whole pixels: the glyph raster, and with it the brightest pixel, moves with a fractional
        // offset, and the craft's own width carries one.
        craft.x = Math.round(target.x + target.width / 2 - craft.w / 2);
        craft.y = Math.round(target.y + target.height / 2 - craft.h / 2);
      }
      for (const c of fleet) {
        c.el.style.opacity = "1";
        c.el.style.transform = `translate(${c.x}px,${c.y}px)`;
      }
      return { family: FAMILIES[pinned.family], drawn: craft.el.innerHTML };
    },
    { target: box, leg, shimmer, stride: PARK_STRIDE, frames: HELD_FRAMES },
  );
  await settle(page);
  return held;
}

// Whether the hold outlasted the reading: the same frame still drawn, and frames still queued
// behind it, so nothing the page did between the shots moved what was measured.
const stillHeld = (page, held) =>
  page.evaluate(
    (drawn) => fleet[0].el.innerHTML === drawn && fleet[0].queue.length > 0,
    held.drawn,
  );

const backgroundOf = (page) =>
  page.locator("#join").evaluate((el) => getComputedStyle(el).backgroundColor);

// Every line the block prints, and the ack in both of its moods.
const haloOf = (page) =>
  page.locator("#join").evaluate((block) => {
    const shadow = (el, pseudo) => getComputedStyle(el, pseudo ?? null).textShadow;
    const ack = block.querySelector("#ack");
    const entry = block.querySelector("#email");
    const printed = {
      head: shadow(block.querySelector(".head")),
      prompt: shadow(block.querySelector(".prompt")),
      email: shadow(entry),
      placeholder: shadow(entry, "::placeholder"),
      go: shadow(block.querySelector("#go")),
      ack: shadow(ack),
    };
    const wasBad = ack.classList.contains("bad");
    ack.classList.add("bad");
    printed.ackBad = shadow(ack);
    ack.classList.toggle("bad", wasBad);
    return printed;
  });

test("the block is glass: a craft behind it still shows, and the copy still reads", async () => {
  const page = await open(3);
  const glass = await page.locator("#join").evaluate((el) => {
    const style = getComputedStyle(el);
    return { background: style.backgroundColor, filter: style.backdropFilter, colour: style.color };
  });
  const tint = glass.background.match(/^rgba\(0, 0, 0, ([\d.]+)\)$/);
  assert.ok(tint, `the panel's background is ${glass.background}: no sky passes an opaque pane`);
  assert.equal(Number(tint[1]), TINT_ALPHA);
  assert.equal(glass.filter, BACKDROP_COMPUTED);
  // Chromium drops the -webkit twin: it is absent from the .panel rule's own cssText, absent from
  // getPropertyValue and has no computed value, so the declaration Safari needs is read off the
  // stylesheet the browser was served, where both must stand and must agree.
  const stylesheet = await page.evaluate(() => document.querySelector("style").textContent);
  assert.deepEqual(
    [...stylesheet.matchAll(/\s(-webkit-)?backdrop-filter:([^;]+);/g)].map((declared) => [
      declared[1] ?? "",
      declared[2].trim(),
    ]),
    [
      ["", BACKDROP_DECLARED],
      ["-webkit-", BACKDROP_DECLARED],
    ],
  );
  // The halo is what holds the copy up against the light the pane passes, and a form control prints
  // with none of the block's own, so every line is read for it: the heading, the prompt, the entry,
  // its placeholder — the dimmest copy the block prints — the button, and the ack in both moods.
  assert.deepEqual(await haloOf(page), {
    head: HALO,
    prompt: HALO,
    email: HALO,
    placeholder: HALO,
    go: HALO,
    ack: ACK_HALO,
    ackBad: ACK_BAD_HALO,
  });
  // The heading prints the panel's own hull: a dimmer grey has less to hold up with.
  assert.equal(
    await page.locator("#join .head").evaluate((el) => getComputedStyle(el).color),
    glass.colour,
  );

  const strip = await boxOf(page, "#ack");
  const swallowing = await page.addStyleTag({
    content: `.panel{background:${SWALLOWING_TINT}}`,
  });
  const swallow = async (on) => {
    await swallowing.evaluate((el, on) => (el.disabled = !on), on);
    await settle(page);
  };
  await swallow(true);
  assert.equal(await backgroundOf(page), SWALLOWING_TINT);
  await swallow(false);
  assert.equal(await backgroundOf(page), glass.background);

  await holdCraftOnLeg(page, null, 0, 0);
  const bare = await peakUnder(page, strip);
  assert.ok(bare <= GLASS_UNLIT_MAX, `the strip printed ${bare} of light with nothing behind it`);

  const copy = linearLuminance(glass.colour);
  for (let leg = 0; leg < GLASS_LEGS; leg++) {
    for (const shimmer of GLASS_PHASES) {
      const held = await holdCraftOnLeg(page, strip, leg, shimmer);
      const passed = await peakUnder(page, strip);
      await swallow(true);
      const swallowed = await peakUnder(page, strip);
      await swallow(false);
      const where = `leg ${leg} phase ${shimmer}, a ${held.family}`;
      assert.ok(await stillHeld(page, held), `${where}: the craft moved while it was read`);
      assert.ok(
        contrast(copy, passed) >= COPY_CONTRAST_MIN,
        `${where}: the pane passed ${passed.toFixed(5)} of light, which the copy holds only ${contrast(copy, passed).toFixed(2)}:1 against`,
      );
      assert.ok(
        passed / swallowed >= CRAFT_THROUGH_GLASS_MIN,
        `${where}: the pane passed ${passed.toFixed(5)}, only ${(passed / swallowed).toFixed(2)}× the ${swallowed.toFixed(5)} a ${SWALLOWING_TINT} pane leaves of it`,
      );
    }
  }
  await page.close();
});

// The floor is worth nothing if a tint it must turn down can clear it, so this ships one and puts the
// same gate over the same held craft, leg by leg. The tint it ships is lighter than the pane the floor
// measures against, so the two readings come off two panes: over the 48 states it passes 1.4× to 1.9×
// what that pane passes — more on every leg, and never the floor's 3×.
test("the glass gate turns down a tint that swallows the craft", async () => {
  const page = await open(3);
  await page.addStyleTag({ content: `.panel{background:${TURNED_DOWN_TINT}}` });
  const strip = await boxOf(page, "#ack");
  const reference = await page.addStyleTag({ content: `.panel{background:${SWALLOWING_TINT}}` });
  const measureAgainst = async (on) => {
    await reference.evaluate((el, on) => (el.disabled = !on), on);
    await settle(page);
  };
  await measureAgainst(true);
  assert.equal(await backgroundOf(page), SWALLOWING_TINT);
  await measureAgainst(false);
  assert.equal(await backgroundOf(page), TURNED_DOWN_TINT);
  for (let leg = 0; leg < GLASS_LEGS; leg++) {
    for (const shimmer of GLASS_PHASES) {
      const held = await holdCraftOnLeg(page, strip, leg, shimmer);
      const passed = await peakUnder(page, strip);
      await measureAgainst(true);
      const swallowed = await peakUnder(page, strip);
      await measureAgainst(false);
      const where = `leg ${leg} phase ${shimmer}, a ${held.family}`;
      assert.ok(await stillHeld(page, held), `${where}: the craft moved while it was read`);
      assert.ok(
        passed > swallowed,
        `${where}: the ${TURNED_DOWN_TINT} pane passed ${passed.toFixed(5)} and the ${SWALLOWING_TINT} pane ${swallowed.toFixed(5)}, so the floor is reading one pane twice`,
      );
      assert.ok(
        passed / swallowed < CRAFT_THROUGH_GLASS_MIN,
        `${where}: a ${TURNED_DOWN_TINT} pane passed ${passed.toFixed(5)}, ${(passed / swallowed).toFixed(2)}× what the floor measures against, which the floor lets through`,
      );
    }
  }
  assert.equal(await backgroundOf(page), TURNED_DOWN_TINT);   // read through that pane throughout
  await page.close();
});

test("the panel copy is the fixed join: Title Case label, terminal prompt, submit verb", async () => {
  const page = await open();
  assert.equal((await page.textContent("#join .head")).trim(), "Join Waitlist");
  assert.equal(await page.getAttribute("#join", "aria-labelledby"), "join-head");
  assert.equal(await page.getAttribute("#join .head", "id"), "join-head");
  assert.equal(await page.getAttribute("#email", "aria-label"), "Email address");
  assert.equal(await page.getAttribute("#email", "placeholder"), "email@work.com");
  assert.equal((await page.textContent("#join .prompt")).trim(), ">");
  // The prompt is plain terminal text: the panel's own color and the panel's own black halo, with
  // no glow of its own — the ack's green one is the only glow the block prints.
  const prompt = await page.locator("#join .prompt").evaluate((el) => ({
    shadow: [...getComputedStyle(el).textShadow.matchAll(/rgba?\([^)]*\)/g)].map((m) => m[0]),
    panelColor: getComputedStyle(el).color === getComputedStyle(el.closest(".panel")).color,
  }));
  assert.deepEqual(prompt, {
    shadow: ["rgb(0, 0, 0)", "rgb(0, 0, 0)", "rgb(0, 0, 0)"],
    panelColor: true,
  });
  // The entry's insertion caret carries no color of its own.
  const caret = await page.locator("#email").evaluate((el) => ({
    caret: getComputedStyle(el).caretColor,
    color: getComputedStyle(el).color,
  }));
  assert.ok(caret.caret === "auto" || caret.caret === caret.color, caret.caret);
  assert.equal((await page.textContent("#go")).trim(), "submit");
  await page.close();
});

test("the join posts to the worker and renders its ack verbatim", async () => {
  const page = await open();
  const before = queued.length;
  const block = await boxOf(page, "#join");
  const entry = await boxOf(page, "#email");
  await page.fill("#email", "Pilot@YourCo.com");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() =>
    document.getElementById("ack").textContent.includes("waitlist"),
  );
  assert.match(
    (await page.textContent("#ack")).trim(),
    /^#\d+ on the waitlist\. We will email you when access opens\.$/,
  );
  // A desktop strip reserves its three lines from rest, so landing an ack moves nothing under the
  // hand: two lines of copy here, three on the narrowest phone below, and a strip that holds three
  // either way. A phone reserves nothing until something prints, which the test below walks.
  assert.deepEqual(await ackRoom(page), { lines: 2, reserved: 3 });
  assert.deepEqual(await boxOf(page, "#join"), block);
  assert.deepEqual(await boxOf(page, "#email"), entry);
  assert.equal(await page.locator("#email").isDisabled(), true);
  assert.equal(await page.locator("#go").isDisabled(), true);
  assert.equal(queued.length, before + 1);
  assert.equal(queued.at(-1).email, "pilot@yourco.com");
  await page.close();
});

// The desktop assertions above hold the same two boxes from rest at 1200px, where the ack takes two
// lines rather than the three it wraps to at 320px. A phone holds them from the press instead: the
// strip reserves nothing while it is empty, and the `Joining…` the submit handler prints takes the
// whole three lines under the press itself — the block's one growth, and the last of them. So the
// wire is held open here to read the block the press leaves, and the ack that answers has to land in
// it without moving a box: three lines of copy at 320px, into the three the press reserved.
test("on the narrowest phone the press reserves the strip, and the ack lands in it", async () => {
  const page = await open(1, PHONE_NARROW);
  assert.deepEqual(await ackRoom(page), { lines: 0, reserved: 0 });
  const rested = await page.locator("#join").evaluate((el) => el.getBoundingClientRect().height);
  assert.equal(Math.round(rested * 100) / 100, PHONE_RESTING_BLOCK_PX);
  assert.equal((await boxOf(page, "#ack")).height, 0);
  // The wire answers when this releases it, so the press's own print stands to be measured alone.
  await page.evaluate(() => {
    const wire = window.fetch;
    window.__answer = null;
    window.fetch = (...call) =>
      new Promise((resolve) => (window.__answer = () => resolve(wire(...call))));
  });
  await page.fill("#email", "narrow@yourco.com");
  await page.click("#go");
  await page.waitForFunction(() =>
    document.getElementById("ack").textContent.startsWith("Joining"),
  );
  assert.deepEqual(await ackRoom(page), { lines: 1, reserved: 3 });
  const pressed = await page.locator("#join").evaluate((el) => el.getBoundingClientRect().height);
  assert.equal(Math.round(pressed * 100) / 100, PHONE_PRINTED_BLOCK_PX);
  const block = await boxOf(page, "#join");
  const entry = await boxOf(page, "#email");
  const button = await boxOf(page, "#go");
  const strip = await boxOf(page, "#ack");
  await page.evaluate(() => window.__answer());
  await page.waitForFunction(() =>
    document.getElementById("ack").textContent.includes("waitlist"),
  );
  assert.deepEqual(await ackRoom(page), { lines: 3, reserved: 3 });
  assert.deepEqual(await boxOf(page, "#join"), block);
  assert.deepEqual(await boxOf(page, "#email"), entry);
  assert.deepEqual(await boxOf(page, "#go"), button);
  assert.deepEqual(await boxOf(page, "#ack"), strip);
  const laid = await page.locator("#join").evaluate((el) => {
    const box = el.getBoundingClientRect();
    return { top: box.top, bottom: box.bottom, viewport: innerHeight };
  });
  assert.ok(
    laid.top >= 0 && laid.bottom <= laid.viewport,
    `the block spans ${laid.top}-${laid.bottom}px of ${laid.viewport}px`,
  );
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
