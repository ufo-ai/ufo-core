import { Sources } from "@/components/ui/sources";

export function ActivitySourcesWeb() {
  return (
    <Sources
      sources={[
        { kind: "web", title: "Release notes", url: "https://github.com/ufo/ufo/releases" },
        { kind: "web", title: "Changelog", url: "https://developer.mozilla.org/en-US/docs/Web" },
        { kind: "web", title: "Migration guide", url: "https://react.dev/blog" },
      ]}
    />
  );
}
