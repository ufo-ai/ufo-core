import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { SCHEME_OPTIONS, pickScheme, useScheme } from "@/lib/scheme";
import { DocPage, DocSection, PropsTable, State, States } from "@/playground/docs";
import { CHAT_PAGES, type Page } from "@/playground/pages";
import {
  ChatSimulator,
  SIMULATOR_NAME,
  SIMULATOR_SLUG,
} from "@/playground/simulator/ChatSimulator";

/** The page draws in one palette or the other; "system" would leave the reader guessing which. */
const PALETTES = SCHEME_OPTIONS.filter((option) => option.scheme !== "system");

const API_REFERENCE = "API reference";
const API_NOTE =
  "A prop is the whole way a page changes this component. Anything it does not list is the " +
  "component's own, and the linter refuses it at the call site.";

const SHELL =
  "grid h-dvh overflow-hidden grid-cols-[var(--container-index)_1fr] " +
  "max-narrow:h-auto max-narrow:grid-cols-1 max-narrow:overflow-visible";
const SIDEBAR =
  "flex h-dvh min-w-0 flex-col overflow-y-auto scrollbar-thin border-e border-edge bg-surface " +
  "max-narrow:sticky max-narrow:top-0 max-narrow:z-10 max-narrow:h-auto max-narrow:flex-row " +
  "max-narrow:flex-wrap max-narrow:items-center max-narrow:gap-x-2xl max-narrow:gap-y-sm " +
  "max-narrow:overflow-visible max-narrow:border-e-0 max-narrow:border-b max-narrow:px-2xl " +
  "max-narrow:py-sm";
const BRAND =
  "flex min-w-0 flex-col gap-sm px-2xl pt-2xl pb-2xl max-narrow:flex-1 max-narrow:flex-row " +
  "max-narrow:items-baseline max-narrow:gap-sm max-narrow:p-0";
const NAV =
  "flex min-h-0 flex-1 flex-col gap-hair overflow-y-auto scrollbar-thin px-2xl pb-2xl " +
  "max-narrow:order-last max-narrow:w-full max-narrow:flex-none max-narrow:flex-row " +
  "max-narrow:overflow-x-auto max-narrow:scrollbar-none max-narrow:scroll-fade-x max-narrow:p-0";
const NAV_LINK =
  "rounded-control px-md py-xs text-label whitespace-nowrap text-ink-soft no-underline " +
  "hover:bg-fill hover:text-ink aria-[current=page]:bg-fill-strong " +
  "aria-[current=page]:font-medium aria-[current=page]:text-ink";
const PICKER = "flex flex-wrap gap-2xs max-narrow:order-none";
/** The states are what the page is for, so the content column takes the whole measure: a second
 *  track for an index costs a `wide` page its second column of states below about 1570px. */
const MAIN =
  "min-w-0 overflow-y-auto px-8xl py-8xl " +
  "max-narrow:overflow-visible max-narrow:px-2xl max-narrow:py-6xl";

/** Every state a chat component can be in, drawn from its own props. A state the page cannot reach
 *  through a prop is a state the component does not own, which is what `shadcn/no-restyle` refuses
 *  at the call sites in `.oxlintrc.json`. */
export function Playground() {
  const scheme = useScheme();
  const [slug, setSlug] = useState(() => location.hash.replace(/^#\/?/, ""));
  useEffect(() => {
    const read = () => setSlug(location.hash.replace(/^#\/?/, ""));
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, []);
  const simulating = slug === SIMULATOR_SLUG;
  const page = CHAT_PAGES.find((held) => held.slug === slug) ?? CHAT_PAGES[0];
  const groups = [...new Set(page.examples.map((shown) => shown.group))];
  return (
    <div className={SHELL}>
      <div className={SIDEBAR}>
        <header className={BRAND}>
          <span className="font-mono text-mono text-ink-quiet max-narrow:hidden">∵ ufo</span>
          <p className="m-0 text-label font-strong text-ink">Chat components</p>
          <div className={PICKER} role="group" aria-label="Colour scheme">
            {PALETTES.map((option) => (
              <Button
                key={option.scheme}
                variant="option"
                size="bar"
                aria-pressed={scheme === option.scheme}
                onClick={() => pickScheme(option.scheme)}
              >
                {option.label}
              </Button>
            ))}
          </div>
        </header>
        <nav aria-label="Chat components" className={NAV}>
          <a
            href={"#/" + SIMULATOR_SLUG}
            aria-current={simulating ? "page" : undefined}
            className={NAV_LINK}
          >
            {SIMULATOR_NAME}
          </a>
          {CHAT_PAGES.map((held) => (
            <a
              key={held.slug}
              href={"#/" + held.slug}
              aria-current={!simulating && held.slug === page.slug ? "page" : undefined}
              className={NAV_LINK}
            >
              {held.name}
            </a>
          ))}
        </nav>
      </div>
      {simulating ? (
        <main className="min-w-0">
          <ChatSimulator />
        </main>
      ) : (
        <main className={MAIN}>
          <Component page={page} groups={groups} />
        </main>
      )}
    </div>
  );
}

function Component({ page, groups }: { page: Page; groups: string[] }) {
  return (
    <DocPage title={page.name} modules={page.modules} description={page.owns}>
      {groups.map((group) => (
        <div key={group} className="min-w-0">
          <DocSection title={group}>
            <States spread={page.spread}>
              {page.examples
                .filter((shown) => shown.group === group)
                .map((shown) => (
                  <State key={shown.title} title={shown.title} replay={shown.replay}>
                    {shown.render()}
                  </State>
                ))}
            </States>
          </DocSection>
        </div>
      ))}
      <div className="min-w-0">
        <DocSection title={API_REFERENCE} description={API_NOTE}>
          <div className="flex min-w-0 flex-col gap-6xl">
            {page.props.map((table) => (
              <PropsTable key={table.of} of={table.of} rows={table.rows} />
            ))}
          </div>
        </DocSection>
      </div>
    </DocPage>
  );
}
