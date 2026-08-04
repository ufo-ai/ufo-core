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
// A landscape window 568px across, with Safari's own chrome taking its share of the height: the
// shortest window the page is served in, and 76px inside the band where the block's copy is taller
// than the room the window leaves it — at this width the block wants 285.83px and the sky takes
// 20px above and below it, so every window under 326px caps. The width is load-bearing as much as
// the height — the panel's padding is a share of it — so both are stated outright rather than
// spread from PHONE.
const PHONE_LANDSCAPE_SHORT = { ...PHONE, viewport: { width: 568, height: 250 } };

async function open(craft = 1, device = DESKTOP) {
  const page = await browser.newPage(device);
  page.on("pageerror", (error) => assert.fail(`page error: ${error}`));
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
const shownIn = (page, ...selectors) =>
  page.locator("#join").evaluate((panel, wanted) => {
    const to2 = (n) => Math.round(n * 100) / 100;
    const outer = panel.getBoundingClientRect();
    const clipTop = outer.top + panel.clientTop;
    const clipBottom = clipTop + panel.clientHeight;
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
            inWindow: spanned(box, 0, innerHeight),
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
    const spanned = (top, bottom) =>
      to2(Math.max(0, Math.min(copy.bottom, bottom) - Math.max(copy.top, top)));
    return {
      "the ack's printed copy": {
        height: to2(copy.bottom - copy.top),
        inClip: spanned(clipTop, clipTop + panel.clientHeight),
        inWindow: spanned(0, innerHeight),
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

// Both short viewports, because the coarse-pointer rules carry 16px type and 44px targets: a phone
// in landscape is the one holding the tallest block.
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

// Shorter than the block, the window is where the plain cap earns itself: uncapped the block's head
// hangs 55.83px off the top of a 568x250 window, and capped it stands 20px inside it. What the cap
// holds back a hand can still roll into view, and what lands in the ack the page rolls in itself.
test("a window shorter than the block caps it, and a hand rolls the rest into view", async () => {
  const page = await open(6, PHONE_LANDSCAPE_SHORT);
  const { width, height } = page.viewportSize();
  const where = `${width}x${height}`;
  const placed = await placementOf(page);
  assert.ok(placed.top >= 0, `the block's head hangs ${-placed.top}px off the top of the window`);
  assert.ok(
    placed.gap >= BOTTOM_GAP_MIN && placed.gap <= BOTTOM_GAP_MAX,
    `${placed.gap}px between the block and the bottom edge`,
  );
  assert.equal(placed.scrolled, true, "the cap did not bind on the shortest window the page serves");
  assert.equal(placed.reachable, true);
  // What the cap holds back here is 76px, the ack's reserved strip bar a fraction, so the two
  // controls a hand acts on stand whole inside it before anything lands.
  assertWhollyShown(await shownIn(page, "#email", "#go"), where);
  await landAJoin(page, "capped@yourco.com");
  const landed = await placementOf(page);
  assert.equal(landed.scrolled, true, "the cap stopped binding once the ack landed");
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
  // already spent part of it: standing the copy whole takes 24 of the panel's 76px and leaves
  // 47.69 of 76.8px of the ack's box inside the port, so the hand's own 52 is what carries the
  // whole box in — which is why the wheel is measured against where the print left the panel and
  // not against zero.
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

// Safari's chrome takes a different share of the screen from phone to phone, so the cap binds across
// a band of window heights rather than at one, and these are the shortest window the page is served
// in and two deeper caps still. Wherever it binds the ack is the copy held back, and the room the
// page spends to roll it in comes out of the heading: the block's own head keeps standing 20px
// inside the window, and so does every control the path left enabled. Nothing here rests on the
// entry being disabled — three of the four paths leave it live, which is why each is walked.
//
// Two things the page cannot buy back at 568x200, both measured: before anything lands, 158px of
// port holds 168.31px of head, entry and button, so the button stands 10.31px short until a hand
// rolls it in; and on a landed join the entry gives up 11.69px of itself to the copy — the one path
// that has taken it away by then.
test("wherever the cap binds, what a report prints reads whole and live controls stay whole", async () => {
  for (const height of [250, 220, 200]) {
    for (const path of REPORT_PATHS) {
      const { viewport } = PHONE_LANDSCAPE_SHORT;
      const page = await open(6, { ...PHONE_LANDSCAPE_SHORT, viewport: { ...viewport, height } });
      const where = `${viewport.width}x${height}, ${path.name}`;
      assert.equal((await placementOf(page)).scrolled, true, `the cap does not bind at ${where}`);
      await path.drive(page, height);
      await page.waitForFunction(
        (opening) => document.getElementById("ack").textContent.startsWith(opening),
        path.opening,
      );
      const reported = await placementOf(page);
      assert.equal(reported.scrolled, true, `the cap stopped binding at ${where}`);
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
// again — and the ack that prints then lands wherever the panel is standing, not at the head. A roll
// that never moved the panel back would leave that copy read and a control the refusal handed back
// clipped: measured at 568x200, the wheel leaves the panel at 126, the resubmit's own focus scroll at
// 124, the print needs 49, and #email shows 0 of 44px inside the port until the print stands the
// panel at what the copy needs. The band is walked because the gap between the two is the cap's, and
// the cap binds across it — 76 against a need of −1 at 250, 106 against 29 at 220.
const REFUSED_ADDRESS = REPORT_PATHS.find((path) => path.name === "a refused address");

test("a report stands its copy and live controls whole from a member's own scroll", async () => {
  for (const height of [250, 220, 200]) {
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

// Room lost after a report is the same clip arriving from the other side, and the window takes it two
// ways. Its bottom edge dragged up walks the port's bottom edge under copy that has already printed
// and changes nothing about the copy: measured on a page that rolled only as it printed, at
// 568x250 → 200 a landed ack held 44.59 of 44.59px inside the port and then 0px of it, 15.69px of it
// still inside the window; at 1200x240 → 180 its 38.39px left the port and the window both,
// `scrollTop` unmoved at 24 and 16. Its side edge dragged in takes the same room without moving the
// port at all: at 568x250 → 320x250 that ack rewraps from two lines onto three, its 44.59px of copy
// becomes 70.19px, and 49.41px is all of it left inside a port that is still 208px, `scrollTop` stuck
// at 24 where the copy now needs 45. A phone whose URL bar comes back, a window edge dragged up and a
// window edge dragged in, because what changed is the room and not the copy.
//
// The room the roll spends is the heading's, as it is when the copy lands: what the resize cannot buy
// back is measured too — at 568x200 a landed join leaves the disabled entry 32.31 of 44px and at
// 1200x180, 23.78 of 35.39px, which is why the controls asserted are the ones the path left live.
const ROOM_LOSING_RESIZES = [
  {
    device: PHONE_LANDSCAPE_SHORT,
    from: { width: 568, height: 250 },
    to: { width: 568, height: 200 },
  },
  {
    device: DESKTOP,
    from: { width: 1200, height: 240 },
    to: { width: 1200, height: 180 },
  },
  {
    device: PHONE_LANDSCAPE_SHORT,
    from: { width: 568, height: 250 },
    to: { width: 320, height: 250 },
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
// window narrowed 568 → 560 at the same height rewraps none of it, so the copy needs the scroll it
// needed before — 74px on a landed join, 49px falling to 48px on the reports that print one line —
// and one grown 200 → 210 hands 10px of port back. In neither may the roll throw away a scroll the
// member made themselves. Both ends are walked on each: the move that must not roll, and then a real
// loss on the same page, which must. 568x200 is a height every one of the four paths rolls from, so on
// each of them the member has somewhere of their own to wheel to. The loss after the growth stops 5px
// above the height the print rolled at — the copy needs 69px at 205 against the 74px it needed at 200
// — so nothing rolls there unless the page took the growth in. Each loss is also taken at a height
// whose port can hold the printed copy and the controls the path left live at once, which is what the
// leg asserts: 560x190 leaves 148px of port, where 560x180 leaves 138px against the 144px from the
// entry's top to the copy's foot and no scroll shows both — 188px is the first height that does, and
// there #email has 1.98px to spare.
const ROOM_KEEPING_RESIZES = [
  {
    name: "the window narrows at the same height",
    to: { width: 560, height: 200 },
    shrunkTo: 190,
  },
  {
    name: "the window grows 10px, short of clearing the copy",
    to: { width: 568, height: 210 },
    shrunkTo: 205,
  },
];
const ROOM_KEEPING_FROM = { width: 568, height: 200 };

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
// member left it, and a resize only ever rolls forward from there. Room the window takes away has to
// come from somewhere, but a member already sitting past what the copy needs is reading it, and
// pulling the panel back to the need would throw their own scroll away. At 568x200 → 180 the copy's
// need goes 49 → 69 while the wheel has left them at 126, so the loss is real and it is behind them.
test("a resize rolls the panel forward or not at all", async () => {
  const page = await open(6, { ...PHONE_LANDSCAPE_SHORT, viewport: { width: 568, height: 200 } });
  const where = "568x200 → 568x180, a refused address, the member at the panel's foot";
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
  await page.setViewportSize({ width: 568, height: 180 });
  await page.waitForFunction(
    ([width, height]) => innerWidth === width && innerHeight === height,
    [568, 180],
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
      };
    });
    assert.deepEqual(placed, { gap, offCentreX: 0 });
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

test("the panel copy is the fixed join: Title Case label, terminal prompt, join verb", async () => {
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
  assert.equal((await page.textContent("#go")).trim(), "join");
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
  // The ack's room is reserved, so landing it moves nothing under the hand: two lines of ack here,
  // three on the narrowest phone below, and a strip that holds three either way.
  assert.deepEqual(await ackRoom(page), { lines: 2, reserved: 3 });
  assert.deepEqual(await boxOf(page, "#join"), block);
  assert.deepEqual(await boxOf(page, "#email"), entry);
  assert.equal(await page.locator("#email").isDisabled(), true);
  assert.equal(await page.locator("#go").isDisabled(), true);
  assert.equal(queued.length, before + 1);
  assert.equal(queued.at(-1).email, "pilot@yourco.com");
  await page.close();
});

// The narrowest phone still in service is 320 CSS px across, and that is where the worker's success
// ack wraps to three lines — the height the strip reserves. The desktop assertions above hold the
// same two boxes at 1200px, where the ack takes two.
const PHONE_NARROW = { ...PHONE, viewport: { width: 320, height: 568 } };

test("a join lands on the narrowest phone without moving the block", async () => {
  const page = await open(1, PHONE_NARROW);
  const block = await boxOf(page, "#join");
  const entry = await boxOf(page, "#email");
  const button = await boxOf(page, "#go");
  // What the reservation costs before a join lands: three lines of empty strip.
  assert.deepEqual(await ackRoom(page), { lines: 0, reserved: 3 });
  const strip = await boxOf(page, "#ack");
  await page.fill("#email", "narrow@yourco.com");
  await page.click("#go");
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
