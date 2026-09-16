import { Button } from "@/components/ui/button";

export function ButtonOptionPressed() {
  return (
    <div className="flex gap-2xs">
      <Button variant="option" aria-pressed>
        Opus
      </Button>
      <Button variant="option" aria-pressed={false}>
        Sonnet
      </Button>
    </div>
  );
}
