import type { ComponentProps, ReactNode } from "react";

import "@/blocks/card.css";

export type CardVariant = "outline" | "muted" | "plain" | "pill";
export type CardSize = "default" | "sm" | "lg";
export type CardAlign = "start" | "end";
export type CardTitleSize = "default" | "lg";
export type CardButtonVariant = "primary" | "secondary";
export type CardButtonSize = "sm" | "default" | "lg";

type Surface = Omit<ComponentProps<"div">, "className">;

/** A surface carrying one self-contained record: a header, what the record is about, and its acts. */
export function Card({
  variant = "outline",
  size = "default",
  inline = false,
  align,
  ...props
}: Surface & { variant?: CardVariant; size?: CardSize; inline?: boolean; align?: CardAlign }) {
  return (
    <div
      className="blk-card"
      data-variant={variant}
      data-size={size}
      data-inline={inline ? "true" : undefined}
      data-align={align}
      {...props}
    />
  );
}

/** A picture across the top of the card, meeting its two upper corners. */
export function CardImage({ src, alt, ratio }: { src: string; alt: string; ratio?: number }) {
  return <img className="blk-card-image" src={src} alt={alt} style={ratio ? { aspectRatio: ratio } : undefined} />;
}

/** The card's head: a title, the prose under it, and an action held at the trailing edge. */
export function CardHeader(props: Surface) {
  return <div className="blk-card-header" {...props} />;
}

/** What the card is, at the label step or at the h1 step. */
export function CardTitle({ size = "default", ...props }: Surface & { size?: CardTitleSize }) {
  return <div className="blk-card-title" data-size={size} {...props} />;
}

/** The line under the title that says what the card holds. */
export function CardDescription(props: Surface) {
  return <div className="blk-card-description" {...props} />;
}

/** What the header offers at its trailing edge, held there however long the title runs. */
export function CardAction(props: Surface) {
  return <div className="blk-card-action" {...props} />;
}

/** What the card is about. `bleed` runs it to the card's edges; `data-font="mono"` sets the mono face. */
export function CardContent({ bleed = false, ...props }: Surface & { bleed?: boolean; "data-font"?: "mono" }) {
  return <div className="blk-card-content" data-bleed={bleed ? "true" : undefined} {...props} />;
}

/** The card's foot, divided from the content by a hairline and holding its acts in a row. */
export function CardFooter(props: Surface) {
  return <div className="blk-card-footer" {...props} />;
}

/** An act on the card, drawn as a pill: `primary` is the filled one, `secondary` the quiet one. */
export function CardButton({
  variant = "primary",
  size = "default",
  disabled,
  children,
  onClick,
}: {
  variant?: CardButtonVariant;
  size?: CardButtonSize;
  disabled?: boolean;
  children: ReactNode;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      className="blk-card-button"
      data-variant={variant}
      data-size={size}
      disabled={disabled}
      aria-disabled={disabled ? true : undefined}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
