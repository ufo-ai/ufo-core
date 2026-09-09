import { createRoot } from "react-dom/client";

import { Portal } from "@/Portal";
import { markDeployment } from "@/lib/mark";
import { heldRum, startRum } from "@/lib/rum";
import { heldScheme, markScheme } from "@/lib/scheme";
import "@/theme.css";

const recorded = heldRum();
/** A task after the mount, not a frame, which a page opened in a hidden tab never runs: the 184 kB SDK
 *  observes with `buffered: true` and reads navigation off `performance`, so a late start loses no load. */
if (recorded) {
  window.setTimeout(() => startRum(recorded), 0);
}

markScheme(heldScheme());
void markDeployment(location.hostname);

const root = document.getElementById("root");
if (!root) throw new Error("the portal page has no #root to mount into");
createRoot(root).render(<Portal />);
