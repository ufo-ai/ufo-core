import starlight from "@astrojs/starlight";
import { defineConfig } from "astro/config";
import starlightBlog from "starlight-blog";

const blogLayout = {
  name: "blog-layout",
  hooks: {
    "config:setup": async ({ addRouteMiddleware, config, updateConfig }) => {
      addRouteMiddleware({
        entrypoint: new URL("./src/blog-layout.ts", import.meta.url).pathname,
        order: "post",
      });
      updateConfig({
        components: {
          ...config.components,
          MarkdownContent: new URL("./src/components/MarkdownContent.astro", import.meta.url)
            .pathname,
        },
      });
    },
  },
};

// One build serves both doors, so every canonical and the sitemap name production. The testing door
// is the same bytes under testing.ufo.ai, which keeps that copy out of the index by pointing at
// its production twin rather than by a rule a crawler may ignore.
export default defineConfig({
  site: "https://ufo.ai",
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
      components: {
        Header: "./src/components/Header.astro",
        PageSidebar: "./src/components/PageSidebar.astro",
        PageTitle: "./src/components/PageTitle.astro",
      },
      plugins: [starlightBlog({ navigation: "none", prefix: "blog", rss: false }), blogLayout],
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
            "docs/getting-started/introduction",
            "docs/getting-started/setup",
            "docs/getting-started/ask",
          ],
        },
        {
          label: "Connecting your systems",
          items: [
            "docs/connectors",
            "docs/connectors/github",
            "docs/connectors/slack",
            "docs/connectors/gmail",
            "docs/connectors/google-calendar",
            "docs/connectors/google-drive",
            "docs/connectors/google-sheets",
            "docs/connectors/notion",
            "docs/connectors/linear",
            "docs/connectors/datadog",
            "docs/connectors/sentry",
            "docs/connectors/mcp",
            "docs/connectors/stripe",
            "docs/connectors/hubspot",
            "docs/connectors/zendesk",
            "docs/connectors/quickbooks",
          ],
        },
        {
          label: "Working with ufo",
          items: [
            "docs/work/web",
            "docs/work/data-ownership",
            "docs/work/slack",
            "docs/work/terminal",
            "docs/work/imessage",
            "docs/work/writing-code",
            "docs/work/reviewing-pull-requests",
            "docs/work/querying-data",
            "docs/work/investigating-incidents",
            "docs/work/getting-good-results",
            "docs/work/troubleshooting",
            "docs/work/sources",
            "docs/work/memory",
            "docs/work/tasks",
            "docs/work/files-sites",
            "docs/work/applications",
          ],
        },
        {
          label: "Cloud sessions",
          items: ["docs/cloud/browser", "docs/cloud/processes"],
        },
        {
          label: "Recipes",
          items: [
            "docs/recipes",
            "docs/recipes/code-change",
            "docs/recipes/pull-request-review",
            "docs/recipes/incident-investigation",
            "docs/recipes/data-analysis",
            "docs/recipes/documentation-audit",
            "docs/recipes/release-notes",
            "docs/recipes/weekly-progress-report",
            "docs/recipes/meeting-brief",
            "docs/recipes/customer-feedback",
            "docs/recipes/create-deliverable",
            "docs/recipes/internal-application",
          ],
        },
        {
          label: "Workspace",
          items: [
            "docs/workspace/members",
            "docs/workspace/permissions",
            "docs/workspace/billing",
            "docs/workspace/troubleshooting",
          ],
        },
        "docs/changelog",
      ],
    }),
  ],
});
