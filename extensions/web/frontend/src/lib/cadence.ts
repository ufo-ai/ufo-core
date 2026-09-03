/** The cadences a screen offers for a cron schedule, and the cron each one is written as.
 *
 *  Cron stores UTC and only the browser knows the member's offset, so this is where a wall-clock
 *  time and a cron field meet. An hour that crosses midnight takes its weekdays with it — a Monday
 *  9pm local that is Tuesday 03:00 UTC fires on Tuesday, and a set that did not move would fire a
 *  day early every week. */

const MINUTES_IN_DAY = 24 * 60;
const MINUTES_IN_HOUR = 60;
const WEEK = 7;
const CRON_FIELD_COUNT = 5;

/** The working week, as cron numbers its days. */
export const WORKING_WEEK = [1, 2, 3, 4, 5];

/** What a cadence with no time of its own fires at, in the member's own clock. */
const DEFAULT_HOUR = 9;

/** Where minutes on a clock land once an offset moves them, and how many days they crossed. */
export function shifted(
  minutes: number,
  by: number,
): { hour: number; minute: number; days: number } {
  const total = minutes + by;
  const settled = ((total % MINUTES_IN_DAY) + MINUTES_IN_DAY) % MINUTES_IN_DAY;
  return {
    hour: Math.floor(settled / MINUTES_IN_HOUR),
    minute: settled % MINUTES_IN_HOUR,
    days: Math.floor(total / MINUTES_IN_DAY),
  };
}

/** The same weekdays, moved by the days a time shift crossed, in cron's own order. */
export function movedDays(days: number[], by: number): number[] {
  return days.map((day) => (((day + by) % WEEK) + WEEK) % WEEK).sort((one, other) => one - other);
}

/** Cron's own range, which an agent's own composition may write where a screen writes a list. */
function span(range: string): number[] {
  const [from, to] = range.split("-").map(Number);
  if (!Number.isInteger(from) || !Number.isInteger(to) || to < from) return [NaN];
  return Array.from({ length: to - from + 1 }, (_, step) => from + step);
}

function dayList(field: string): number[] {
  return field.split(",").flatMap((one) => (one.includes("-") ? span(one) : [Number(one)]));
}

/** One shape a schedule is read and written in. `custom` is the cron itself: an agent composes any
 *  cron the extension validates, and a guess at which offer it meant would put a cadence on the
 *  screen the schedule does not hold. */
export type Cadence =
  | { mode: "interval"; hours: number }
  | { mode: "daily"; hour: number; minute: number }
  | { mode: "weekdays"; hour: number; minute: number }
  | { mode: "weekly"; weekday: number; hour: number; minute: number }
  | { mode: "custom"; cron: string };

export type CadenceMode = Cadence["mode"];

/** The offers, in the order a member reads them: how often, then which days. */
export const CADENCE_MODES: { mode: CadenceMode; label: string }[] = [
  { mode: "interval", label: "Interval" },
  { mode: "daily", label: "Daily" },
  { mode: "weekdays", label: "Weekdays" },
  { mode: "weekly", label: "Weekly" },
  { mode: "custom", label: "Custom" },
];

/** The intervals an hour field divides evenly. A step cron does not divide by lands one fire short
 *  at the end of every day, which is not the cadence the words promise. */
export const INTERVAL_HOURS = [1, 2, 3, 4, 6, 8, 12];

/** The cron a cadence fires on, in UTC. */
export function cronOf(cadence: Cadence, offsetMinutes: number): string {
  if (cadence.mode === "custom") return cadence.cron;
  if (cadence.mode === "interval") {
    return cadence.hours === 1 ? "0 * * * *" : `0 */${cadence.hours} * * *`;
  }
  const moved = shifted(cadence.hour * MINUTES_IN_HOUR + cadence.minute, offsetMinutes);
  const clock = `${moved.minute} ${moved.hour}`;
  if (cadence.mode === "daily") return `${clock} * * *`;
  const days =
    cadence.mode === "weekdays"
      ? movedDays(WORKING_WEEK, moved.days)
      : movedDays([cadence.weekday], moved.days);
  return `${clock} * * ${days.join(",")}`;
}

/** The cadence a cron stands for, in the member's own clock — the read `cronOf` writes back, so a
 *  schedule the member set is recognised as the offer they took. */
export function cadenceOf(cron: string, offsetMinutes: number): Cadence {
  const custom: Cadence = { mode: "custom", cron };
  const field = cron.trim().split(/\s+/);
  if (field.length !== CRON_FIELD_COUNT) return custom;
  const [minute, hour, day, month, weekday] = field;
  if (day !== "*" || month !== "*") return custom;
  const past = Number(minute);
  if (!Number.isInteger(past)) return custom;
  if (hour === "*" || hour.startsWith("*/")) {
    if (weekday !== "*" || past !== 0) return custom;
    const step = hour === "*" ? 1 : Number(hour.slice(2));
    return INTERVAL_HOURS.includes(step) ? { mode: "interval", hours: step } : custom;
  }
  const struck = Number(hour);
  if (!Number.isInteger(struck)) return custom;
  const local = shifted(struck * MINUTES_IN_HOUR + past, -offsetMinutes);
  const clock = { hour: local.hour, minute: local.minute };
  if (weekday === "*") return { mode: "daily", ...clock };
  const days = movedDays(dayList(weekday), local.days);
  if (days.some((one) => !Number.isInteger(one))) return custom;
  if (days.join(",") === WORKING_WEEK.join(",")) return { mode: "weekdays", ...clock };
  if (days.length === 1) return { mode: "weekly", weekday: days[0], ...clock };
  return custom;
}

/** The time a cadence fires at, or null where it names no time of day. */
export function clockOf(cadence: Cadence): { hour: number; minute: number } | null {
  return cadence.mode === "daily" || cadence.mode === "weekdays" || cadence.mode === "weekly"
    ? { hour: cadence.hour, minute: cadence.minute }
    : null;
}

/** The same cadence read as another offer, keeping what the two shapes share. A `custom` cadence
 *  holds no clock of its own, so the clock is read out of the cron it holds rather than defaulted:
 *  a pick to Custom and back is not a member typing a new time. */
export function asMode(
  cadence: Cadence,
  mode: CadenceMode,
  cron: string,
  offsetMinutes: number,
): Cadence {
  if (mode === cadence.mode) return cadence;
  const held = clockOf(cadence) ?? clockOf(cadenceOf(cron, offsetMinutes));
  const clock = held ?? { hour: DEFAULT_HOUR, minute: 0 };
  switch (mode) {
    case "interval":
      return { mode, hours: 1 };
    case "daily":
    case "weekdays":
      return { mode, ...clock };
    case "weekly":
      return { mode, weekday: WORKING_WEEK[0], ...clock };
    case "custom":
      return { mode, cron };
  }
}

/** A weekday's name, in the member's own locale. Jan 4 2026 is a Sunday, which is cron's day 0. */
export function weekdayName(day: number): string {
  return new Date(2026, 0, 4 + day).toLocaleDateString(undefined, { weekday: "long" });
}

function timeLabel(hour: number, minute: number): string {
  return new Date(2026, 0, 5, hour, minute).toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
  });
}

/** What a cadence is called, in the member's own clock. It names the recurrence and nothing else:
 *  a schedule's first fire is its next cron occurrence, so a label promising a run now promises a
 *  run that never comes. */
export function labelOf(cadence: Cadence): string {
  switch (cadence.mode) {
    case "custom":
      return cadence.cron;
    case "interval":
      return cadence.hours === 1 ? "Every hour" : `Every ${cadence.hours} hours`;
    case "daily":
      return `Daily at ${timeLabel(cadence.hour, cadence.minute)}`;
    case "weekdays":
      return `Weekdays at ${timeLabel(cadence.hour, cadence.minute)}`;
    case "weekly":
      return `${weekdayName(cadence.weekday)}s at ${timeLabel(cadence.hour, cadence.minute)}`;
  }
}

/** `09:00` — the wall-clock string a `time` box reads and writes. */
export function timeValue(hour: number, minute: number): string {
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

/** The clock a `time` box holds, or null while the member has it half typed. */
export function clockFromValue(value: string): { hour: number; minute: number } | null {
  const [hour, minute] = value.split(":").map(Number);
  if (!Number.isInteger(hour) || !Number.isInteger(minute)) return null;
  return { hour, minute };
}
