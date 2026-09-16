import { Sources } from "@/components/ui/sources";

export function ActivitySourcesUnknown() {
  return (
    <Sources
      sources={[
        { kind: "web", title: "A page whose favicon never decodes", url: "https://nothing.invalid" },
        { kind: "workspace", title: "A record with no provider", ref: "record:1" },
      ]}
    />
  );
}
