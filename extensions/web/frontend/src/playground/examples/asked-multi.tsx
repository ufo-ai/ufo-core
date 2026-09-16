import { Asked } from "@/components/ui/asked";
import type { ChatQuestion } from "@/lib/types";

const QUESTION: ChatQuestion = {
  turn_id: "turn-2f41",
  title: "Release note",
  icon: "propylon",
  questions: [
    {
      question: "Who reads the note first?",
      multi_select: true,
      options: [
        { label: "Support" },
        { label: "Sales" },
        { label: "Everyone in the workspace" },
      ],
    },
  ],
};

export function AskedMulti() {
  return <Asked question={QUESTION} held={false} onAnswer={() => {}} />;
}
