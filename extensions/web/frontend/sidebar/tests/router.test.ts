import { renderHook } from "@testing-library/react";
import { expect, test } from "vitest";

import { sectionHash } from "@/lib/route";
import { heldRoute, startRouter, useRoute } from "@/lib/router";
import { heldTrack, holdTrack, type TrackScreen } from "@/lib/tracks";

const CONNECTORS: TrackScreen = "section:connectors";
const RECORD = "connection/g1";

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
