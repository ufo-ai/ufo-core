import { Asked } from "@/components/ui/asked";
import type { ChatQuestion } from "@/lib/types";

const QUESTION: ChatQuestion = {
  turn_id: "turn-2f41",
  title: "Release note",
  icon: "propylon",
  questions: [
    {
      question: "Which branch does the note cover?",
      options: [
        { label: "release", description: "What ships on Thursday." },
        { label: "main", description: "Everything merged since 2.13." },
      ],
    },
    {
      question: "Who reads it first?",
      options: [{ label: "Support" }, { label: "Everyone in the workspace" }],
    },
    {
      question: "When does it go out?",
      free_text_only: true,
    },
  ],
};

export function AskedStepper() {
  return <Asked question={QUESTION} held={false} onAnswer={() => {}} />;
}
