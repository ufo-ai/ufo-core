import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

/** Where the member is, said as the path they took to get here: the place they came from, held
 *  back, and the thing they are looking at, in full ink. It is the screen's title and its way out
 *  at once — a member who wants the list they arrived from should not have to find it again in the
 *  sidebar, and a title that is only a title makes them.
 *
 *  The last crumb is the page itself, so it is not a link. It carries `aria-current` instead,
 *  which is how a reader is told the path ends here rather than by a link that goes nowhere. */
export function Breadcrumb(props: ComponentProps<"nav">) {
  return <nav aria-label="Breadcrumb" data-slot="breadcrumb" {...props} />;
}

export function BreadcrumbList({ className, ...props }: ComponentProps<"ol">) {
  return (
    <ol
      data-slot="breadcrumb-list"
      className={cn(
        "m-0 flex list-none flex-wrap items-center gap-sm p-0 text-label text-ink-soft",
        className,
      )}
      {...props}
    />
  );
}

export function BreadcrumbItem({ className, ...props }: ComponentProps<"li">) {
  return (
    <li
      data-slot="breadcrumb-item"
      className={cn("inline-flex items-center gap-sm", className)}
      {...props}
    />
  );
}

/** A crumb the member can go back to, drawn as the address it stands at: the step is navigation, so
 *  it is an anchor a member can open in a second tab or press with a modifier, and inside a framed
 *  page it is the link that page's own handler carries over the bridge. A step no address reaches is
 *  not this — it is the landmark's name, and text is all it is. */
export function BreadcrumbLink({ className, ...props }: ComponentProps<"a">) {
  return (
    <a
      data-slot="breadcrumb-link"
      className={cn(
        "m-0 cursor-pointer p-0 text-inherit no-underline",
        "transition-colors duration-100 ease-control hover:text-ink",
        className,
      )}
      {...props}
    />
  );
}

export function BreadcrumbPage({ className, ...props }: ComponentProps<"span">) {
  return (
    <span
      data-slot="breadcrumb-page"
      aria-current="page"
      className={cn("min-w-0 truncate font-medium text-ink", className)}
      {...props}
    />
  );
}

/** The mark between two crumbs, drawn rather than spoken: a reader hears the path from the list
 *  it is in, and a slash read out between every pair is noise. */
export function BreadcrumbSeparator({ className, ...props }: ComponentProps<"li">) {
  return (
    <li
      aria-hidden
      data-slot="breadcrumb-separator"
      className={cn("select-none text-ink-faint", className)}
      {...props}
    >
      /
    </li>
  );
}
