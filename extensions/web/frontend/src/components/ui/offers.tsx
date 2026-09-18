import { useState } from "react";
import { IconCornerDownRight } from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import { Ticker } from "@/components/ui/ticker";

/* Every row that sends a sentence draws the same arrow: the mark says the press sends the words,
   which is the one thing these rows have to say before they are read. */
export function AskMark() {
  return <IconCornerDownRight className="size-(--size-glyph) shrink-0 text-ink-soft" aria-hidden />;
}

/* A row reads as the few words of its hook and sends the prompt behind it, which states the work in
   full; the rule says the words under it are the member's rather than the agent's. */
export function OfferRows({
  offers,
  onPress,
}: {
  offers: { hook: string; prompt: string }[];
  onPress: (prompt: string) => void;
}) {
  if (!offers.length) return null;
  return (
    <div
      data-testid="follow-ups"
      className="flex flex-col items-start gap-sm border-t border-edge pt-6xl"
    >
      {offers.map((offer) => (
        <OfferRow key={offer.hook} hook={offer.hook} onPress={() => onPress(offer.prompt)} />
      ))}
    </div>
  );
}

function OfferRow({ hook, onPress }: { hook: string; onPress: () => void }) {
  const [asks, setAsks] = useState(0);
  return (
    <Button
      variant="framed"
      size="reading"
      onClick={onPress}
      className="max-w-full justify-start"
      onPointerEnter={() => setAsks((asked) => asked + 1)}
      onPointerLeave={() => setAsks(0)}
      onFocus={() => setAsks((asked) => asked + 1)}
      onBlur={() => setAsks(0)}
    >
      <AskMark />
      <Ticker asks={asks} className="min-w-0 flex-1 max-narrow:whitespace-normal">
        {hook}
      </Ticker>
    </Button>
  );
}
