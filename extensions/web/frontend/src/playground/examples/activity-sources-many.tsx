import { Sources } from "@/components/ui/sources";

const READ = [
  "github",
  "linear",
  "notion",
  "slack",
  "figma",
  "confluence",
  "asana",
  "airtable",
  "datadog",
  "sentry",
];

export function ActivitySourcesMany() {
  return (
    <Sources
      sources={READ.map((provider, at) => ({
        kind: "workspace" as const,
        title: provider,
        ref: provider + ":" + at,
        provider,
      }))}
    />
  );
}
