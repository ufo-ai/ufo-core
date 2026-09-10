import { useEffect, useState, type ReactNode } from "react";
import { IconMoon, IconSun } from "@tabler/icons-react";

import "@/blocks/tokens.css";
import "@/blocks/docs/docs.css";
import { PAGES } from "@/blocks/docs/pages";

type Scheme = "light" | "dark";

function useHash(): string {
  const [hash, setHash] = useState(() => location.hash.replace(/^#\/?/, ""));
  useEffect(() => {
    const read = () => setHash(location.hash.replace(/^#\/?/, ""));
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, []);
  return hash;
}

/** The component reference: one page per block, each with live examples and their source. */
export function Docs() {
  const hash = useHash();
  const [scheme, setScheme] = useState<Scheme>("dark");
  useEffect(() => {
    document.documentElement.dataset.scheme = scheme;
  }, [scheme]);
  const [slug, ...rest] = hash.split("/");
  const page = PAGES.find((p) => p.slug === slug) ?? PAGES[0];
  return (
    <div className="blk-root blk-docs">
      <nav className="blk-docs-nav" aria-label="Components">
        <div className="blk-docs-nav-title">
          <a href="#/">Blocks</a>
          <button
            type="button"
            className="blk-docs-nav-toggle"
            aria-label={scheme === "dark" ? "Switch to light" : "Switch to dark"}
            onClick={() => setScheme(scheme === "dark" ? "light" : "dark")}
          >
            {scheme === "dark" ? <IconSun size={16} stroke={1.5} /> : <IconMoon size={16} stroke={1.5} />}
          </button>
        </div>
        {PAGES.filter((p) => p.slug !== "").map((p) => (
          <a key={p.slug} href={`#/${p.slug}`} aria-current={p.slug === page.slug ? "page" : undefined}>
            {p.title}
          </a>
        ))}
      </nav>
      <main className="blk-docs-main">{page.render(rest.join("/"))}</main>
    </div>
  );
}

export function DocPage({ title, description, children }: { title: string; description: string; children?: ReactNode }) {
  return (
    <article className="blk-docs-page">
      <header>
        <h1>{title}</h1>
        <p>{description}</p>
      </header>
      {children}
    </article>
  );
}

export function DocSection({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <section className="blk-docs-section">
      <h2>{title}</h2>
      {description ? <p>{description}</p> : null}
      {children}
    </section>
  );
}

export function Example({
  title,
  description,
  code,
  align,
  children,
}: {
  title?: string;
  description?: string;
  code: string;
  align?: "center" | "start";
  children: ReactNode;
}) {
  const [tab, setTab] = useState<"preview" | "code">("preview");
  return (
    <div className="blk-example">
      {title ? <h3>{title}</h3> : null}
      {description ? <p>{description}</p> : null}
      <div className="blk-example-tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === "preview"} onClick={() => setTab("preview")}>
          Preview
        </button>
        <button type="button" role="tab" aria-selected={tab === "code"} onClick={() => setTab("code")}>
          Code
        </button>
      </div>
      {tab === "preview" ? (
        <div className="blk-example-frame" data-align={align ?? "center"}>
          {children}
        </div>
      ) : (
        <pre className="blk-example-code">
          <code>{code.trim()}</code>
        </pre>
      )}
    </div>
  );
}

export type PropRow = { name: string; type: string; default?: string; description: string };

export function PropsTable({ rows }: { rows: PropRow[] }) {
  return (
    <div className="blk-props-scroll">
      <table className="blk-props">
        <thead>
          <tr>
            <th>Prop</th>
            <th>Type</th>
            <th>Default</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.name}>
              <td>
                <code>{r.name}</code>
              </td>
              <td>
                <code>{r.type}</code>
              </td>
              <td>{r.default ? <code>{r.default}</code> : "—"}</td>
              <td>{r.description}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
