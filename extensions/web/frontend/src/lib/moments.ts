const ISO_MOMENT = /^\d{4}-\d{2}-\d{2}T/;
const MINUTE_MS = 60_000;
const HOUR_MS = 3_600_000;
const DAY_MS = 86_400_000;

export function isMoment(value: string): boolean {
  return ISO_MOMENT.test(value);
}

/** One timestamp as a table cell reads it: the calendar minute it names, in UTC as it was sent. */
export function day(iso: string | null): string | null {
  return iso == null ? null : iso.slice(0, 16).replace("T", " ");
}

/** One timestamp as a line of prose reads it: how long ago it happened, or how long until it will. */
export function relativeMoment(raw: string, now: Date): string {
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
