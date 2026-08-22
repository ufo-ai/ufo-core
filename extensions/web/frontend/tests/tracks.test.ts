import { beforeEach, expect, test, vi } from "vitest";

import { TRACK_MAX_SLOTS, heldTrack, holdTrack, type TrackScreen } from "@/lib/tracks";

const AGENT = "9c4d0f2a-1b3e-4a5c-8d7f-6e2b1a0c9d84";
const OTHER = "5b1e8c37-2d4a-4f61-9a08-3c7d5e2f1b06";
const ARTIFACTS: TrackScreen = "section:connectors";
const TEAM: TrackScreen = "workspace:team";
const APP: TrackScreen = `agent:${AGENT}`;

beforeEach(() => {
  sessionStorage.clear();
});

test("a track reads back on the screen that held it, whichever screen that is", () => {
  holdTrack(ARTIFACTS, [
    "object/" + AGENT + "/report/august",
    "object/" + AGENT + "/report/july",
  ]);
  holdTrack(TEAM, ["member/developer@local.test"]);
  holdTrack(APP, [
    "run/3f1c",
    "object/" + AGENT + "/note/brief",
    "member/developer@local.test",
  ]);

  expect(heldTrack(ARTIFACTS)).toEqual([
    "object/" + AGENT + "/report/august",
    "object/" + AGENT + "/report/july",
  ]);
  expect(heldTrack(TEAM)).toEqual(["member/developer@local.test"]);
  expect(heldTrack(APP)).toEqual([
    "run/3f1c",
    "object/" + AGENT + "/note/brief",
    "member/developer@local.test",
  ]);
});

/** A lane names the app its record is read in as well as the record, so one track stands two apps'
 *  records and comes back holding both. */
test("a track of two apps' lanes reads back with each lane in its own app", () => {
  holdTrack(ARTIFACTS, [
    "object/" + AGENT + "/scheduled_task/morning-digest",
    "object/" + OTHER + "/memory/roadmap",
  ]);

  expect(heldTrack(ARTIFACTS)).toEqual([
    "object/" + AGENT + "/scheduled_task/morning-digest",
    "object/" + OTHER + "/memory/roadmap",
  ]);
});

test("a detour through another screen leaves the first screen's track standing", () => {
  holdTrack(ARTIFACTS, ["object/" + AGENT + "/report/august"]);
  holdTrack(TEAM, ["member/developer@local.test"]);
  holdTrack(TEAM, []);

  expect(heldTrack(ARTIFACTS)).toEqual(["object/" + AGENT + "/report/august"]);
});

test("a screen that has held nothing reads as an empty track", () => {
  expect(heldTrack("section:connectors")).toEqual([]);
  expect(heldTrack(`agent:${AGENT}`)).toEqual([]);
});

test("holding a track again states the order rather than adding to it", () => {
  holdTrack(ARTIFACTS, ["a", "b", "c"]);
  holdTrack(ARTIFACTS, ["c", "a"]);

  expect(heldTrack(ARTIFACTS)).toEqual(["c", "a"]);
});

test("closing every slot drops the screen's key rather than holding an empty line", () => {
  holdTrack(ARTIFACTS, ["object/" + AGENT + "/report/august"]);
  holdTrack(ARTIFACTS, []);

  expect(heldTrack(ARTIFACTS)).toEqual([]);
  expect(sessionStorage.getItem("ufo.track.section:connectors")).toBeNull();
});

/** A track the address holds whole and the store cut short is the same fault one layer down: the
 *  screen would come back holding fewer lanes than the link the member followed states. The verbs
 *  that open a lane stop at the cap, so a row past it is one a caller made up, and it raises there
 *  rather than reading back shorter than it was written. */
test("a track past the cap is refused where it was made up, never held short", () => {
  const opened = Array.from(
    { length: TRACK_MAX_SLOTS + 1 },
    (_, at) => `object/${AGENT}/report/${at}`,
  );

  expect(() => holdTrack(ARTIFACTS, opened)).toThrow();
  expect(sessionStorage.getItem("ufo.track.section:connectors")).toBeNull();

  const full = opened.slice(0, TRACK_MAX_SLOTS);
  holdTrack(ARTIFACTS, full);
  expect(heldTrack(ARTIFACTS)).toEqual(full);
});

/** A screen the member can work from is worth more than an arrangement nothing here can name, and
 *  a row of lanes this module did not write is exactly that: the lanes would stand under records
 *  nothing can resolve. The mark is what tells the two apart. */
test("a value this store did not write reads as an empty track and is dropped", () => {
  for (const corrupt of [
    '{"slots":["object/report/august"]}\n',
    "ufo.track\n" + AGENT + "\nobject/scheduled_task/morning-digest",
    "ufo.track\n\nobject/report/august",
    "ufo.lanes\n\nobject/" + AGENT + "/report/august",
    "ufo.lanes\n" + "x".repeat(300),
    "ufo.lanes\n" + Array.from({ length: 40 }, (_, at) => `object/report/${at}`).join("\n"),
    "ufo.lanes",
    "",
  ]) {
    sessionStorage.setItem("ufo.track.section:connectors", corrupt);
    expect(heldTrack(ARTIFACTS)).toEqual([]);
    expect(sessionStorage.getItem("ufo.track.section:connectors")).toBeNull();
  }
});

test("a slot id the store cannot round trip is refused where it was made up", () => {
  const refused = (slots: string[]) => () => holdTrack(ARTIFACTS, slots);
  expect(refused(["object/" + AGENT + "/report/august", ""])).toThrow();
  expect(refused(["object/" + AGENT + "/report/august\nobject/" + AGENT + "/report/july"])).toThrow();
  expect(refused(["x".repeat(257)])).toThrow();
  expect(sessionStorage.getItem("ufo.track.section:connectors")).toBeNull();
});

test("a track naming one slot twice is refused where it was made up, and dropped on the way back", () => {
  expect(() =>
    holdTrack(ARTIFACTS, [
      "object/" + AGENT + "/report/august",
      "object/" + AGENT + "/report/august",
    ]),
  ).toThrow();
  expect(sessionStorage.getItem("ufo.track.section:connectors")).toBeNull();

  sessionStorage.setItem(
    "ufo.track.section:connectors",
    ["ufo.lanes", "object/" + AGENT + "/report/august", "object/" + AGENT + "/report/august"].join(
      "\n",
    ),
  );
  expect(heldTrack(ARTIFACTS)).toEqual([]);
  expect(sessionStorage.getItem("ufo.track.section:connectors")).toBeNull();
});

test("a browser that refuses the write leaves the screen standing rather than raising", () => {
  const refused = () => {
    throw new DOMException("quota exceeded", "QuotaExceededError");
  };
  vi.stubGlobal("sessionStorage", {
    getItem: () => null,
    setItem: refused,
    removeItem: refused,
    clear: () => {},
  });

  expect(() => holdTrack(ARTIFACTS, ["object/" + AGENT + "/report/august"])).not.toThrow();
  expect(() => holdTrack(ARTIFACTS, [])).not.toThrow();
  expect(heldTrack(ARTIFACTS)).toEqual([]);
});
