import { Sources } from "@/components/ui/sources";

export function ActivitySourcesWorkspace() {
  return (
    <Sources
      sources={[
        { kind: "workspace", title: "Handover", ref: "notion:1", provider: "notion" },
        { kind: "workspace", title: "Release plan", ref: "linear:2", provider: "linear" },
        { kind: "workspace", title: "Support thread", ref: "slack:3", provider: "slack" },
      ]}
    />
  );
}
