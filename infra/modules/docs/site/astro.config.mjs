import starlight from "@astrojs/starlight";
import { defineConfig } from "astro/config";

// One build serves both doors, so every canonical and the sitemap name production. The testing door
// is the same bytes under docs.testing.ufo.ai, which keeps that copy out of the index by pointing at
// its production twin rather than by a rule a crawler may ignore.
export default defineConfig({
  site: "https://docs.ufo.ai",
  integrations: [
    starlight({
      title: "UFO",
      logo: {
        light: "./src/assets/lockup.svg",
        dark: "./src/assets/lockup-on-dark.svg",
        replacesTitle: true,
      },
      description: "Learn how to use ufo, connect your tools, automate work, and manage your workspace.",
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
        {
          label: "Getting started",
          items: [
            "getting-started/introduction",
            "getting-started/setup",
            "getting-started/ask",
          ],
        },
        {
          label: "Connecting your systems",
          items: [
            "connectors",
            "connectors/github",
            "connectors/slack",
            "connectors/gmail",
            "connectors/google-calendar",
            "connectors/google-drive",
            "connectors/google-sheets",
            "connectors/notion",
            "connectors/linear",
            "connectors/datadog",
            "connectors/sentry",
            "connectors/mcp",
            "connectors/stripe",
            "connectors/hubspot",
            "connectors/zendesk",
            "connectors/quickbooks",
          ],
        },
        {
          label: "Working with ufo",
          items: [
            "work/web",
            "work/slack",
            "work/terminal",
            "work/imessage",
            "work/writing-code",
            "work/reviewing-pull-requests",
            "work/querying-data",
            "work/investigating-incidents",
            "work/getting-good-results",
            "work/troubleshooting",
            "work/sources",
            "work/memory",
            "work/tasks",
            "work/files-sites",
            "work/applications",
          ],
        },
        {
          label: "Cloud sessions",
          items: ["cloud/browser", "cloud/processes"],
        },
        {
          label: "Recipes",
          items: [
            "recipes",
            "recipes/code-change",
            "recipes/pull-request-review",
            "recipes/incident-investigation",
            "recipes/data-analysis",
            "recipes/documentation-audit",
            "recipes/release-notes",
            "recipes/weekly-progress-report",
            "recipes/meeting-brief",
            "recipes/customer-feedback",
            "recipes/create-deliverable",
            "recipes/internal-application",
          ],
        },
        {
          label: "Workspace",
          items: [
            "workspace/members",
            "workspace/permissions",
            "workspace/billing",
            "workspace/troubleshooting",
          ],
        },
        "changelog",
      ],
    }),
  ],
});
