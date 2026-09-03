import { expect, test } from "vitest";

import { asMode, cadenceOf, cronOf, labelOf, type Cadence } from "@/lib/cadence";

/** The offset the member's browser reports, in the sign `getTimezoneOffset` uses: minutes to add to
 *  a local clock to reach UTC. */
const CHICAGO = 300;

test("a cadence is written as the cron it fires on, in UTC", () => {
  expect(cronOf({ mode: "interval", hours: 1 }, CHICAGO)).toBe("0 * * * *");
  expect(cronOf({ mode: "interval", hours: 6 }, CHICAGO)).toBe("0 */6 * * *");
  expect(cronOf({ mode: "daily", hour: 9, minute: 0 }, CHICAGO)).toBe("0 14 * * *");
  expect(cronOf({ mode: "weekdays", hour: 9, minute: 30 }, 0)).toBe("30 9 * * 1,2,3,4,5");
  expect(cronOf({ mode: "weekly", weekday: 1, hour: 9, minute: 0 }, 0)).toBe("0 9 * * 1");
  expect(cronOf({ mode: "custom", cron: "15 2,14 * * *" }, CHICAGO)).toBe("15 2,14 * * *");
});

/** An hour the offset carries over midnight takes its weekdays with it: a Monday 9pm in Chicago is
 *  Tuesday 02:00 UTC, and a day that did not move would fire a week's worth of runs early. */
test("an hour that crosses midnight moves the days it fires on", () => {
  expect(cronOf({ mode: "weekly", weekday: 1, hour: 21, minute: 0 }, CHICAGO)).toBe("0 2 * * 2");
  expect(cadenceOf("0 2 * * 2", CHICAGO)).toEqual({
    mode: "weekly",
    weekday: 1,
    hour: 21,
    minute: 0,
  });
});

test.each<Cadence>([
  { mode: "interval", hours: 1 },
  { mode: "interval", hours: 12 },
  { mode: "daily", hour: 7, minute: 45 },
  { mode: "weekdays", hour: 18, minute: 0 },
  { mode: "weekly", weekday: 6, hour: 23, minute: 30 },
])("a cadence read back off its own cron is the cadence that wrote it", (cadence) => {
  expect(cadenceOf(cronOf(cadence, CHICAGO), CHICAGO)).toEqual(cadence);
});

/** A cron no offer stands for is read as the cron itself, rather than as the nearest offer: a
 *  cadence on the screen the schedule does not hold would be changed by being looked at. */
test.each([
  "15 2,14 * * *",
  "0 */5 * * *",
  "0 9 1 * *",
  "0 9 * * 1,3",
  "not a cron",
])("a cron no offer stands for reads as custom", (cron) => {
  expect(cadenceOf(cron, CHICAGO)).toEqual({ mode: "custom", cron });
});

test("a cadence is named in the member's own clock", () => {
  expect(labelOf({ mode: "interval", hours: 1 })).toBe("Every hour");
  expect(labelOf({ mode: "interval", hours: 3 })).toBe("Every 3 hours");
  expect(labelOf({ mode: "daily", hour: 9, minute: 0 })).toBe("Daily at 9:00 AM");
  expect(labelOf({ mode: "weekdays", hour: 17, minute: 30 })).toBe("Weekdays at 5:30 PM");
  expect(labelOf({ mode: "weekly", weekday: 1, hour: 9, minute: 0 })).toBe("Mondays at 9:00 AM");
  expect(labelOf({ mode: "custom", cron: "15 2,14 * * *" })).toBe("15 2,14 * * *");
});

/** A `custom` cadence is the cron itself and names no clock, so a read of it as a timed offer takes
 *  the clock out of that cron. A default hour there moves every later fire of a task whose time the
 *  member never touched. */
test("a cadence read off a custom pick keeps the clock the cron holds", () => {
  const custom: Cadence = { mode: "custom", cron: "30 12 * * *" };
  expect(asMode(custom, "daily", custom.cron, CHICAGO)).toEqual({
    mode: "daily",
    hour: 7,
    minute: 30,
  });
  expect(asMode(custom, "weekly", custom.cron, CHICAGO)).toEqual({
    mode: "weekly",
    weekday: 1,
    hour: 7,
    minute: 30,
  });
});

/** A cron no offer stands for holds no clock to keep, so the offer it is read as takes the default
 *  hour rather than a time read out of a cron that names none. */
test("a cadence read off a cron no offer stands for takes the default hour", () => {
  expect(
    asMode({ mode: "custom", cron: "15 2,14 * * *" }, "daily", "15 2,14 * * *", CHICAGO),
  ).toEqual({ mode: "daily", hour: 9, minute: 0 });
});
