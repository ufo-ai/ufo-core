import type { ReactNode } from "react";

import { Card } from "@/components/ui/card";

export function Handoff({ children }: { children: ReactNode }) {
  return (
    <Card className="mt-sm max-w-said gap-sm">
      {children}
    </Card>
  );
}
