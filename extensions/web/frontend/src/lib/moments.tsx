const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const ISO_MOMENT = /^\d{4}-\d{2}-\d{2}T/;
const MINUTE_MS = 60_000;
const HOUR_MS = 3_600_000;
const DAY_MS = 86_400_000;
const YEAR_MS = 365 * DAY_MS;
const MINUTES_AN_HOUR = 60;
const HOURS_A_DAY = 24;

export function isMoment(value: string): boolean {
  return ISO_MOMENT.test(value);
}

/** The one way a date reads in this portal: `Aug 7 2026`, the calendar day it names, in UTC as it
 *  was sent. Read off the ISO string rather than through `Date`, so no reader's zone shifts a
 *  stamp across midnight and no locale reorders the parts. */
export function day(iso: string | null): string | null {
  if (iso == null) return null;
  const [year, month, date] = iso.slice(0, 10).split("-");
  return MONTHS[Number(month) - 1] + " " + Number(date) + " " + year;
}

/** The whole stamp in the member's own zone, named with the zone it reads in: a member asks the
 *  hover what time this was for them, and a stamp in UTC makes them do the arithmetic. */
export function fullMoment(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  const local = localMoment(at);
  return (day(local) ?? local) + " at " + clockOf(local) + " " + zone(at);
}

/** The stamp a member reads on a message, in their own zone: `1:45 PM` today, `Aug 30, 11:02 AM`
 *  on any other day, and carrying the year when that day fell in another one. Shifted the way
 *  `fullMoment` shifts it, so a line a member reads and the hover behind it name one instant. */
export function stampMoment(iso: string): string | null {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return null;
  const local = localMoment(at);
  const today = localMoment(new Date());
  if (local.slice(0, 10) === today.slice(0, 10)) return clockOf(local);
  return dateOf(local, today) + ", " + clockOf(local);
}

function dateOf(local: string, today: string): string {
  const [year, month, date] = local.slice(0, 10).split("-");
  const named = MONTHS[Number(month) - 1] + " " + Number(date);
  return year === today.slice(0, 4) ? named : named + " " + year;
}

const MERIDIEM_PIVOT = 12;

export function clockOf(local: string): string {
  const hour = Number(local.slice(11, 13));
  const minute = local.slice(14, 16);
  const meridiem = hour < MERIDIEM_PIVOT ? "AM" : "PM";
  return (hour % MERIDIEM_PIVOT || MERIDIEM_PIVOT) + ":" + minute + " " + meridiem;
}

export function localMoment(at: Date): string {
  return new Date(at.getTime() - at.getTimezoneOffset() * MINUTE_MS).toISOString();
}

function zone(at: Date): string {
  const parts = new Intl.DateTimeFormat("en-US", { timeZoneName: "short" }).formatToParts(at);
  return parts.find((part) => part.type === "timeZoneName")?.value ?? "";
}

function relativeMoment(raw: string, now: Date): string {
  const at = new Date(raw);
  if (Number.isNaN(at.getTime())) return raw;
  const ahead = at.getTime() - now.getTime();
  const span = Math.abs(ahead);
  if (span < MINUTE_MS) return "now";
  const size =
    span < HOUR_MS
      ? Math.round(span / MINUTE_MS) + "m"
      : span < DAY_MS
        ? Math.round(span / HOUR_MS) + "h"
        : Math.round(span / DAY_MS) + "d";
  return ahead > 0 ? "in " + size : size + " ago";
}

/** A rail row's moment: today reads as the age of the thread and any earlier day names itself with
 *  the clock time, so one glance separates this morning from last Tuesday. Local to the reader,
 *  since the question the card answers is when this was for them. */
export function rowMoment(raw: string, now: Date): string {
  const at = new Date(raw);
  if (Number.isNaN(at.getTime())) return raw;
  if (at.toDateString() === now.toDateString()) return relativeMoment(raw, now);
  return at.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** A span a member watches run: `42s`, `1m 42s`, `2h 5m`. Seconds are dropped past the hour, where a
 *  counter that moves every second reads as noise rather than progress. */
export function spanMoment(ms: number): string {
  const span = Math.max(0, Math.floor(ms / 1000));
  if (span < 60) return span + "s";
  if (span < 3600) return Math.floor(span / 60) + "m " + (span % 60) + "s";
  return Math.floor(span / 3600) + "h " + Math.floor((span % 3600) / 60) + "m";
}

/** The one age the portal draws: `now`, then one number and one letter — `4m`, `2h`, `1d`, `8d` —
 *  up to a year, and the calendar day itself past that, where a count of days has stopped being a
 *  distance a reader can hold. A stamp ahead of now carries `in`, because a next run and a last
 *  one would otherwise read as the same fact in the same column. */
export function friendlyMoment(raw: string, now: Date): string {
  const at = new Date(raw);
  if (Number.isNaN(at.getTime())) return raw;
  const ahead = at.getTime() - now.getTime();
  const span = Math.abs(ahead);
  if (span < MINUTE_MS) return "now";
  if (span >= YEAR_MS) return day(raw) ?? raw;
  // Truncating a future span reads `in 2h` the millisecond a run three hours out is scheduled; the
  // unit comes off the rounded count, or 23 and a half hours out reads `in 24h` for an hour.
  const whole = ahead > 0 ? Math.round : Math.floor;
  const minutes = whole(span / MINUTE_MS);
  const hours = whole(span / HOUR_MS);
  const size =
    minutes < MINUTES_AN_HOUR
      ? minutes + "m"
      : hours < HOURS_A_DAY
        ? hours + "h"
        : whole(span / DAY_MS) + "d";
  return ahead > 0 ? "in " + size : size;
}

/** A record's time wherever the portal draws one: friendly where it is read, and the whole stamp
 *  under the pointer for the reader who came for the day and the minute. The element carries the
 *  machine-readable stamp with it, so a reader that does not hover still reaches the moment. */
export function Moment({ at }: { at: string | null }) {
  if (!at) return null;
  return (
    <time dateTime={at} title={fullMoment(at)}>
      {friendlyMoment(at, new Date())}
    </time>
  );
}

/** The same age `Moment` draws, for a row that states its stamp without a label to name it. */
export function Age({ at }: { at: string }) {
  return (
    <time dateTime={at} title={fullMoment(at)}>
      {friendlyMoment(at, new Date())}
    </time>
  );
}
