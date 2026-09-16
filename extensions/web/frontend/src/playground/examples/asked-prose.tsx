import { Asked } from "@/components/ui/asked";
import type { ChatQuestion } from "@/lib/types";

const QUESTION: ChatQuestion = {
  turn_id: "turn-2f41",
  title: "Billing migration",
  icon: "akhet",
  questions: [
    {
      header: "Invoices",
      question: "Which invoices should the migration leave alone?",
      allow_attachments: true,
      options: [
        { label: "A list of invoice numbers", description: "One per line." },
        { label: "The export support sent you", description: "Attach the file." },
      ],
    },
  ],
};

export function AskedProse() {
  return <Asked question={QUESTION} held={false} onAnswer={() => {}} />;
}
