import { OfferRows } from "@/components/ui/offers";

const OFFERS = [
  {
    hook: "Draft the billing migration note and send it to support, sales and the workspace",
    prompt:
      "Draft the note for the October billing migration, send it to support and sales, and post it to the workspace once both have read it.",
  },
];

export function OfferLong() {
  return (
    <div className="max-w-(--container-card)">
      <OfferRows offers={OFFERS} onPress={() => {}} />
    </div>
  );
}
