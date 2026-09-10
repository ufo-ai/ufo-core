import type { ReactNode } from "react";

import "@/blocks/avatar.css";

export type AvatarSize = 16 | 24 | 32;

/** A round avatar: an image, or a fallback of initials or a gradient. */
export function Avatar({
  src,
  alt,
  fallback,
  gradient,
  size = 16,
}: {
  src?: string;
  alt: string;
  fallback?: ReactNode;
  gradient?: [string, string];
  size?: AvatarSize;
}) {
  const style = gradient ? { background: `linear-gradient(135deg, ${gradient[0]}, ${gradient[1]})` } : undefined;
  return (
    <span className="blk-avatar" data-size={size} role="img" aria-label={alt} style={style}>
      {src ? <img src={src} alt="" /> : fallback}
    </span>
  );
}

/** Overlapping avatars, each shifted left by half its width. */
export function AvatarStack({ children }: { children: ReactNode }) {
  return <span className="blk-avatar-stack">{children}</span>;
}
