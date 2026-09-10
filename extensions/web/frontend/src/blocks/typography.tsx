import type { ReactNode } from "react";

import "@/blocks/typography.css";

/** A prose column that styles the document elements inside it to the block type scale. */
export function Prose({
  width = "prose",
  font = "sans",
  align = "start",
  children,
}: {
  width?: "prose" | "full";
  font?: "sans" | "mono";
  align?: "start" | "center";
  children: ReactNode;
}) {
  return (
    <div className="blk-prose" data-width={width} data-font={font} data-align={align}>
      {children}
    </div>
  );
}

export type CodeBlockVariant = "filled" | "plain";

/** A code listing, on a muted ground or bare on the page, labelled with its language. */
export function CodeBlock({
  children,
  language,
  variant = "filled",
  wrap,
}: {
  children: ReactNode;
  language?: string;
  variant?: CodeBlockVariant;
  wrap?: boolean;
}) {
  return (
    <pre
      className="blk-code"
      data-language={language}
      data-variant={variant}
      data-wrap={wrap ? "true" : undefined}
    >
      <code className={language ? `language-${language}` : undefined}>{children}</code>
    </pre>
  );
}

/** An image centred on a letterboxed ground, with an optional caption. */
export function Figure({ src, alt, caption }: { src: string; alt: string; caption?: string }) {
  return (
    <figure className="blk-figure">
      <span className="blk-figure-frame">
        <img src={src} alt={alt} />
      </span>
      {caption ? <figcaption>{caption}</figcaption> : null}
    </figure>
  );
}
