import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

export type MeterTone = "primary" | "secondary";

export type MeterPart = {
  label: string;
  value: number;
  tone: MeterTone;
};

const TONES: Record<MeterTone, string> = {
  primary: "bg-live",
  secondary: "bg-blocked",
};

/** A share is announced as its share, because the values a caller passes are whatever it counts in: a
 *  bar drawn from 2680 and 460 sits under the words $31.40 of 10000. Parts summing past the whole are held at it. */
export function Meter({
  label,
  parts,
  of,
  className,
  ...props
}: ComponentProps<"div"> & { label: string; parts: readonly MeterPart[]; of: number }) {
  const whole = of > 0 ? of : 1;
  const drawn = parts.filter((part) => part.value > 0);
  const counted = drawn.reduce((sum, part) => sum + part.value, 0);
  const held = counted > whole ? whole / counted : 1;
  return (
    <div
      data-slot="meter"
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={whole}
      aria-valuenow={Math.min(counted, whole)}
      aria-valuetext={drawn
        .map((part) => `${part.label} ${Math.round((part.value * held * 100) / whole)}%`)
        .join(", ")}
      className={cn(
        "flex h-2xs w-full min-w-0 items-stretch overflow-hidden rounded-row bg-fill",
        className,
      )}
      {...props}
    >
      {drawn.map((part, cell) => (
        <span
          key={`${cell}:${part.label}`}
          aria-hidden
          data-slot="meter-part"
          data-tone={part.tone}
          className={TONES[part.tone]}
          style={{ width: `${((part.value * held) / whole) * 100}%` }}
        />
      ))}
    </div>
  );
}
