import { createRoot } from "react-dom/client";

import { Portal } from "@/Portal";
import "@/theme.css";

const root = document.getElementById("root");
if (!root) throw new Error("the portal page has no #root to mount into");
createRoot(root).render(<Portal />);
