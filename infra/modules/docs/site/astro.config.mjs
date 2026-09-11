import starlight from "@astrojs/starlight";
import { defineConfig } from "astro/config";

// One build serves both doors, so every canonical and the sitemap name production. The testing door
// is the same bytes under docs.testing.ufo.ai, which keeps that copy out of the index by pointing at
// its production twin rather than by a rule a crawler may ignore.
export default defineConfig({
  site: "https://docs.ufo.ai",
  integrations: [
    starlight({
      title: "ufo",
      description: "How to use ufo: signing in, where you talk to the agent, connecting accounts, memory, scheduled work, and what a workspace pays.",
      customCss: ["./src/styles/brand.css"],
      // The code theme carries hexes of its own, and the house style admits no colour that is not a
      // token. The mono face already comes from `--sl-font-mono`.
      expressiveCode: {
        styleOverrides: {
          codeBackground: "var(--color-field)",
          borderColor: "var(--color-edge)",
        },
      },
      head: [
        {
          tag: "link",
          attrs: {
            rel: "icon",
            href: "/favicon.svg",
            type: "image/svg+xml",
            media: "(prefers-color-scheme: light)",
          },
        },
        {
          tag: "link",
          attrs: {
            rel: "icon",
            href: "/favicon-dark.svg",
            type: "image/svg+xml",
            media: "(prefers-color-scheme: dark)",
          },
        },
      ],
      sidebar: [
        { label: "Start", items: ["start", "surfaces"] },
        { label: "Using ufo", items: ["accounts", "memory", "tasks", "files", "apps"] },
        { label: "Your workspace", items: ["members", "billing", "not-yet"] },
      ],
    }),
  ],
});
