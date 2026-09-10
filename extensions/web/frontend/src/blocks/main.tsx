import { createRoot } from "react-dom/client";

import { Docs } from "@/blocks/docs/Docs";

const root = document.getElementById("root");
if (!root) throw new Error("blocks.html has no #root to mount into");
createRoot(root).render(<Docs />);
