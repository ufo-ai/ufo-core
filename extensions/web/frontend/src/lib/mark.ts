/** Which deploy answered the page, read off the host the browser is on — the one thing that tells
 *  three otherwise identical portals apart. Production is the deploy the brand belongs to, so it
 *  wears the mark as drawn and every other deploy wears it in that deploy's colour: a member with
 *  a local tab beside a staging tab sees which is which before reading either. */
export type Deployment = "production" | "testing" | "local";

const LOCAL_HOST = "localhost";
const LOOPBACK_HOST = "127.0.0.1";
const TESTING_HOST = "testing.flyingobject.ai";

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

/** The mark drawn in one colour. Both brand marks state their fill in a single rule, so the
 *  geometry has one source and only the colour moves; a mark that states none is a mark this
 *  cannot paint, and says so rather than hanging the wrong icon on the tab. */
export function markedFill(mark: string, color: string): string {
  if (!FILL.test(mark)) throw new Error("the brand mark declares no fill to paint");
  return mark.replace(FILL, "fill: " + color);
}

/** Repaint the tab icons the page already carries. The links name the built marks, so the fetch
 *  reads the same asset the browser drew and nothing else holds a copy of the geometry. A session
 *  the surface refuses hands back no mark to paint, and the page keeps the one it was built with. */
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
