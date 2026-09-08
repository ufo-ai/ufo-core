const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const ISO_MOMENT = /^\d{4}-\d{2}-\d{2}T/;
const MINUTE_MS = 60_000;
const HOUR_MS = 3_600_000;
const DAY_MS = 86_400_000;
const NEAR_DAYS = 7;

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
