import { expect, test } from "vitest";

import { asMode, cadenceOf, cronOf, labelOf, type Cadence } from "@/lib/cadence";

/** `getTimezoneOffset`'s sign: minutes to add to a local clock to reach UTC. */
const CHICAGO = 300;

test("a cadence is written as the cron it fires on, in UTC", () => {
  expect(cronOf({ mode: "interval", hours: 1 }, CHICAGO)).toBe("0 * * * *");
  expect(cronOf({ mode: "interval", hours: 6 }, CHICAGO)).toBe("0 */6 * * *");
  expect(cronOf({ mode: "daily", hour: 9, minute: 0 }, CHICAGO)).toBe("0 14 * * *");
  expect(cronOf({ mode: "weekdays", hour: 9, minute: 30 }, 0)).toBe("30 9 * * 1,2,3,4,5");
  expect(cronOf({ mode: "weekly", weekday: 1, hour: 9, minute: 0 }, 0)).toBe("0 9 * * 1");
  expect(cronOf({ mode: "custom", cron: "15 2,14 * * *" }, CHICAGO)).toBe("15 2,14 * * *");
});

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

test("a cadence read off a cron no offer stands for takes the default hour", () => {
  expect(
    asMode({ mode: "custom", cron: "15 2,14 * * *" }, "daily", "15 2,14 * * *", CHICAGO),
  ).toEqual({ mode: "daily", hour: 9, minute: 0 });
});
