import { OfferRows } from "@/components/ui/offers";

const OFFERS = [
  {
    hook: "Send it to support",
    prompt: "Send the 2.14 release note to support and tell me when they have read it.",
  },
];

export function OfferOne() {
  return <OfferRows offers={OFFERS} onPress={() => {}} />;
}
