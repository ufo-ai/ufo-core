/** Cron stores UTC and only the browser knows the member's offset. An hour that crosses midnight takes
 *  its weekdays with it, and a set that did not move would fire a day early every week. */

const MINUTES_IN_DAY = 24 * 60;
const MINUTES_IN_HOUR = 60;
const WEEK = 7;
const CRON_FIELD_COUNT = 5;

/** Cron's day-of-month field stands for one day in every month, so a task set on a day past the
 *  28th skips the months that are shorter. */
const MONTH_DAYS = 31;

export const WORKING_WEEK = [1, 2, 3, 4, 5];

const DEFAULT_HOUR = 9;

const FIRST_DAY = 1;

export const MONTH_DAY_NUMBERS = Array.from({ length: MONTH_DAYS }, (_, step) => step + 1);

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

export function movedDays(days: number[], by: number): number[] {
  return days.map((day) => (((day + by) % WEEK) + WEEK) % WEEK).sort((one, other) => one - other);
}

export function movedDay(day: number, by: number): number {
  return ((((day - 1 + by) % MONTH_DAYS) + MONTH_DAYS) % MONTH_DAYS) + 1;
}

/** The day before the 1st belongs to the month before it, which cron cannot name, so a run the
 *  offset moves back past the 1st keeps its day and fires at that UTC day's first minute. */
function monthlyCron(day: number, moved: { hour: number; minute: number; days: number }): string {
  const settled = day + moved.days;
  if (settled < FIRST_DAY) return `0 0 ${day} * *`;
  const named = settled > MONTH_DAYS ? settled - MONTH_DAYS : settled;
  return `${moved.minute} ${moved.hour} ${named} * *`;
}

function span(range: string): number[] {
  const [from, to] = range.split("-").map(Number);
  if (!Number.isInteger(from) || !Number.isInteger(to) || to < from) return [NaN];
  return Array.from({ length: to - from + 1 }, (_, step) => from + step);
}

function dayList(field: string): number[] {
  return field.split(",").flatMap((one) => (one.includes("-") ? span(one) : [Number(one)]));
}

export type Cadence =
  | { mode: "interval"; hours: number }
  | { mode: "daily"; hour: number; minute: number }
  | { mode: "weekdays"; hour: number; minute: number }
  | { mode: "weekly"; weekday: number; hour: number; minute: number }
  | { mode: "monthly"; day: number; hour: number; minute: number }
  | { mode: "custom"; cron: string };

export type CadenceMode = Cadence["mode"];

export const CADENCE_MODES: { mode: CadenceMode; label: string }[] = [
  { mode: "interval", label: "Interval" },
  { mode: "daily", label: "Daily" },
  { mode: "weekdays", label: "Weekdays" },
  { mode: "weekly", label: "Weekly" },
  { mode: "monthly", label: "Monthly" },
  { mode: "custom", label: "Custom" },
];

export const INTERVAL_HOURS = [1, 2, 3, 4, 6, 8, 12];

export function cronOf(cadence: Cadence, offsetMinutes: number): string {
  if (cadence.mode === "custom") return cadence.cron;
  if (cadence.mode === "interval") {
    return cadence.hours === 1 ? "0 * * * *" : `0 */${cadence.hours} * * *`;
  }
  const moved = shifted(cadence.hour * MINUTES_IN_HOUR + cadence.minute, offsetMinutes);
  const clock = `${moved.minute} ${moved.hour}`;
  if (cadence.mode === "daily") return `${clock} * * *`;
  if (cadence.mode === "monthly") return monthlyCron(cadence.day, moved);
  const days =
    cadence.mode === "weekdays"
      ? movedDays(WORKING_WEEK, moved.days)
      : movedDays([cadence.weekday], moved.days);
  return `${clock} * * ${days.join(",")}`;
}

export function cadenceOf(cron: string, offsetMinutes: number): Cadence {
  const custom: Cadence = { mode: "custom", cron };
  const field = cron.trim().split(/\s+/);
  if (field.length !== CRON_FIELD_COUNT) return custom;
  const [minute, hour, day, month, weekday] = field;
  if (month !== "*") return custom;
  const monthDay = day === "*" ? null : Number(day);
  if (monthDay !== null && (!Number.isInteger(monthDay) || weekday !== "*")) return custom;
  const past = Number(minute);
  if (!Number.isInteger(past)) return custom;
  if (hour === "*" || hour.startsWith("*/")) {
    if (monthDay !== null || weekday !== "*" || past !== 0) return custom;
    const step = hour === "*" ? 1 : Number(hour.slice(2));
    return INTERVAL_HOURS.includes(step) ? { mode: "interval", hours: step } : custom;
  }
  const struck = Number(hour);
  if (!Number.isInteger(struck)) return custom;
  const local = shifted(struck * MINUTES_IN_HOUR + past, -offsetMinutes);
  const clock = { hour: local.hour, minute: local.minute };
  if (monthDay !== null) return { mode: "monthly", day: movedDay(monthDay, local.days), ...clock };
  if (weekday === "*") return { mode: "daily", ...clock };
  const days = movedDays(dayList(weekday), local.days);
  if (days.some((one) => !Number.isInteger(one))) return custom;
  if (days.join(",") === WORKING_WEEK.join(",")) return { mode: "weekdays", ...clock };
  if (days.length === 1) return { mode: "weekly", weekday: days[0], ...clock };
  return custom;
}

export function clockOf(cadence: Cadence): { hour: number; minute: number } | null {
  return cadence.mode === "interval" || cadence.mode === "custom"
    ? null
    : { hour: cadence.hour, minute: cadence.minute };
}

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
    case "monthly":
      return { mode, day: FIRST_DAY, ...clock };
    case "custom":
      return { mode, cron };
  }
}

export function weekdayName(day: number): string {
  return new Date(2026, 0, 4 + day).toLocaleDateString(undefined, { weekday: "long" });
}

export const HOURS_OF_DAY = Array.from({ length: 24 }, (_, hour) => hour);

export function timeLabel(hour: number, minute: number): string {
  return new Date(2026, 0, 5, hour, minute).toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
  });
}

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
    case "monthly":
      return `Day ${cadence.day} of the month at ${timeLabel(cadence.hour, cadence.minute)}`;
  }
}

export function timeValue(hour: number, minute: number): string {
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

export function clockFromValue(value: string): { hour: number; minute: number } | null {
  const [hour, minute] = value.split(":").map(Number);
  if (!Number.isInteger(hour) || !Number.isInteger(minute)) return null;
  return { hour, minute };
}

/** A monthly task carries a day of the month, and a date states that day: the first date from this
 *  month on that the day exists, so a 31st never reads as a month that has no 31st. */
export function startDateValue(day: number, from: Date): string {
  const year = from.getFullYear();
  let month = from.getMonth();
  while (new Date(year, month + 1, 0).getDate() < day) month += 1;
  const at = new Date(year, month, day);
  const part = (value: number) => String(value).padStart(2, "0");
  return `${at.getFullYear()}-${part(at.getMonth() + 1)}-${part(at.getDate())}`;
}

export function dayFromDateValue(value: string): number | null {
  const day = Number(value.split("-")[2]);
  return Number.isInteger(day) && day >= 1 && day <= MONTH_DAYS ? day : null;
}
