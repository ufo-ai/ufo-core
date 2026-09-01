import { createRoot } from "react-dom/client";

import { Portal } from "@/Portal";
import { markDeployment } from "@/lib/mark";
import { heldRum, startRum } from "@/lib/rum";
import { heldScheme, markScheme } from "@/lib/scheme";
import "@/theme.css";

const recorded = heldRum();
if (recorded) startRum(recorded);

markScheme(heldScheme());
void markDeployment(location.hostname);

const root = document.getElementById("root");
if (!root) throw new Error("the portal page has no #root to mount into");
createRoot(root).render(<Portal />);
