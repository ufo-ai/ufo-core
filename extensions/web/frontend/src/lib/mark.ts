export type Deployment = "production" | "testing" | "local";

const LOCAL_HOST = "localhost";
const LOOPBACK_HOST = "127.0.0.1";
const TESTING_HOST = "testing.ufo.ai";

const DEPLOYMENT_MARKS: Record<Deployment, string | null> = {
  production: null,
  testing: "#FF6700",
  local: "#0095FF",
};

const FILL = /fill:\s*#[0-9A-Fa-f]{3,8}/;

export function deployment(host: string): Deployment {
  if (host === LOCAL_HOST || host === LOOPBACK_HOST || host.endsWith("." + LOCAL_HOST)) {
    return "local";
  }
  if (host === TESTING_HOST || host.endsWith("." + TESTING_HOST)) return "testing";
  return "production";
}

export function markedFill(mark: string, color: string): string {
  if (!FILL.test(mark)) throw new Error("the brand mark declares no fill to paint");
  return mark.replace(FILL, "fill: " + color);
}

export async function markDeployment(host: string): Promise<void> {
  const color = DEPLOYMENT_MARKS[deployment(host)];
  if (!color) return;
  const links = document.querySelectorAll<HTMLLinkElement>('link[rel="icon"]');
  await Promise.all(
    [...links].map(async (link) => {
      const answer = await fetch(link.href);
      if (!answer.ok) return;
      const painted = markedFill(await answer.text(), color);
      link.href = "data:image/svg+xml," + encodeURIComponent(painted);
    }),
  );
}
