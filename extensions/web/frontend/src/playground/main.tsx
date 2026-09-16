import { createRoot } from "react-dom/client";

import { Playground } from "@/playground/Playground";
import { heldScheme, markScheme } from "@/lib/scheme";
import "@/theme.css";

markScheme(heldScheme());

const root = document.getElementById("root");
if (!root) throw new Error("playground.html has no #root to mount into");
createRoot(root).render(<Playground />);
