import { IconCornerDownRight } from "@tabler/icons-react";

import { PressRow } from "@/components/ui/pressrow";

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
    <div data-testid="follow-ups" className="mt-2xl flex flex-col border-t border-edge pt-lg">
      {offers.map((offer) => (
        <PressRow
          key={offer.hook}
          glyph={<AskMark />}
          line={offer.hook}
          onPress={() => onPress(offer.prompt)}
        />
      ))}
    </div>
  );
}
