const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const ISO_MOMENT = /^\d{4}-\d{2}-\d{2}T/;
const MINUTE_MS = 60_000;
const HOUR_MS = 3_600_000;
const DAY_MS = 86_400_000;
const NEAR_DAYS = 7;
const WEEK_MS = 7 * DAY_MS;
const MONTH_MS = 30 * DAY_MS;
const YEAR_MS = 365 * DAY_MS;

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

export function fullMoment(iso: string): string {
  return (day(iso) ?? iso) + " at " + iso.slice(11, 16) + " UTC";
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

/** A span a member watches run: `42s`, `1m 42s`, `2h 5m`. Seconds are dropped past the hour, where a
 *  counter that moves every second reads as noise rather than progress. */
export function spanMoment(ms: number): string {
  const span = Math.max(0, Math.floor(ms / 1000));
  if (span < 60) return span + "s";
  if (span < 3600) return Math.floor(span / 60) + "m " + (span % 60) + "s";
  return Math.floor(span / 3600) + "h " + Math.floor((span % 3600) / 60) + "m";
}

function dayNumber(year: number, month: number, date: number): number {
  return Date.UTC(year, month - 1, date) / DAY_MS;
}

export function friendlyMoment(raw: string, now: Date): string {
  const at = new Date(raw);
  if (Number.isNaN(at.getTime())) return raw;
  const ahead = at.getTime() - now.getTime();
  if (Math.abs(ahead) < DAY_MS) return relativeMoment(raw, now);
  const [year, month, date] = raw.slice(0, 10).split("-").map(Number);
  const days =
    dayNumber(year, month, date) -
    dayNumber(now.getUTCFullYear(), now.getUTCMonth() + 1, now.getUTCDate());
  if (days === -1) return "Yesterday";
  if (days === 1) return "Tomorrow";
  if (Math.abs(days) < NEAR_DAYS) return days > 0 ? "in " + days + "d" : -days + "d ago";
  return day(raw) ?? raw;
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

/** The distance from now as one number and one letter — `Now`, `4m`, `2h`, `3d`, `2w`, `5mo`,
 *  `1y` — for a column every row of a list carries, where the words beside it are what the member
 *  came to read. The whole stamp stands under the pointer. */
export function ageMoment(raw: string, now: Date): string {
  const at = new Date(raw);
  if (Number.isNaN(at.getTime())) return raw;
  const span = Math.abs(now.getTime() - at.getTime());
  if (span < MINUTE_MS) return "Now";
  if (span < HOUR_MS) return Math.floor(span / MINUTE_MS) + "m";
  if (span < DAY_MS) return Math.floor(span / HOUR_MS) + "h";
  if (span < WEEK_MS) return Math.floor(span / DAY_MS) + "d";
  if (span < MONTH_MS) return Math.floor(span / WEEK_MS) + "w";
  if (span < YEAR_MS) return Math.floor(span / MONTH_MS) + "mo";
  return Math.floor(span / YEAR_MS) + "y";
}

export function Age({ at }: { at: string }) {
  return (
    <time dateTime={at} title={fullMoment(at)}>
      {ageMoment(at, new Date())}
    </time>
  );
}
