import { renderHook } from "@testing-library/react";
import { expect, test } from "vitest";

import { homeHash, sectionHash } from "@/lib/route";
import { heldRoute, startRouter, useRoute } from "@/lib/router";
import { heldTrack, holdTrack, type TrackScreen } from "@/lib/tracks";

const CONNECTORS: TrackScreen = "section:connectors";
const RECORD = "connection/g1";
const HOME: TrackScreen = "home";
const LANE = "cf0c1a53-0a4e-4f4f-9c37-2a9a5f5a2f01";

/** The address answers until the router starts, so the screen the address names is drawn on the
 *  first render with nothing written to hold it there. The arrival is the router's act and not the
 *  render's: a render that landed it would write the address and the track store while React
 *  renders, and the store a screen reads its route from would be written by the read of it. */
test("the address answers the first render, and that render writes nothing", () => {
  holdTrack(CONNECTORS, [RECORD]);
  location.hash = sectionHash("connectors");
  const steps = history.length;

  const { result } = renderHook(() => useRoute());

  expect(result.current).toEqual({ kind: "section", section: "connectors", place: {} });
  expect(location.hash).toBe(sectionHash("connectors"));
  expect(heldTrack(CONNECTORS)).toEqual([RECORD]);
  expect(history.length).toBe(steps);
});

/** Starting the router lands the arrival: the address states no track, so the store hands back the
 *  track the screen was left holding and the address is written to match in the same tick, leaving
 *  the two no frame to stand apart in. The address is replaced rather than pushed — coming back to a
 *  screen is not a new place, and Back leaves the screen instead of walking the member through their
 *  own arrivals at it. */
test("startRouter lands the arrival on the track the screen was left holding", () => {
  holdTrack(CONNECTORS, [RECORD]);
  location.hash = sectionHash("connectors");
  const steps = history.length;

  const stop = startRouter();

  try {
    expect(location.hash).toBe(sectionHash("connectors", { opens: [RECORD] }));
    expect(heldRoute()).toEqual({
      kind: "section",
      section: "connectors",
      place: { opens: [RECORD] },
    });
    expect(history.length).toBe(steps);
  } finally {
    stop();
  }
});

/** Home holds its lanes the way every other screen holds its slots, so the row a member arranged is
 *  the row they come back to, and the address states it from the tick they arrive. */
test("home lands on the lanes it was left holding", () => {
  holdTrack(HOME, [LANE]);
  location.hash = homeHash();

  const stop = startRouter();

  try {
    expect(location.hash).toBe(homeHash({ opens: [LANE] }));
    expect(heldRoute()).toEqual({ kind: "home", place: { opens: [LANE] } });
  } finally {
    stop();
  }
});
