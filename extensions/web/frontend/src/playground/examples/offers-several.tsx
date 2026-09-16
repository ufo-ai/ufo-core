import { OfferRows } from "@/components/ui/offers";

const OFFERS = [
  {
    hook: "Send it to support",
    prompt: "Send the 2.14 release note to support and tell me when they have read it.",
  },
  {
    hook: "Keep this note for next release",
    prompt: "Keep this note as the shape every release note should take from now on.",
  },
  {
    hook: "Watch the release branch",
    prompt: "Tell me whenever something merges into the release branch before Thursday.",
  },
];

export function OffersSeveral() {
  return <OfferRows offers={OFFERS} onPress={() => {}} />;
}
