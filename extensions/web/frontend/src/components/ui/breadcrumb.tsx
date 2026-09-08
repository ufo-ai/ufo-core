import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

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
