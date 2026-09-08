import { renderHook } from "@testing-library/react";
import { expect, test } from "vitest";

import { sectionHash } from "@/lib/route";
import { heldRoute, startRouter, useRoute } from "@/lib/router";
import { heldTrack, holdTrack, type TrackScreen } from "@/lib/tracks";

const CONNECTORS: TrackScreen = "section:connectors";
const RECORD = "connection/g1";

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
