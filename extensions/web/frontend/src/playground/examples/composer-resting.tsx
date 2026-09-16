import {
  PromptInput,
  PromptInputAttach,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputToolbar,
} from "@/components/ui/prompt-input";

export function ComposerResting() {
  return (
    <PromptInput onSend={() => true} className="w-full">
      <PromptInputTextarea value="" onChange={() => {}} placeholder="Ask anything" />
      <PromptInputToolbar>
        <PromptInputAttach />
        <PromptInputSubmit stops={false} />
      </PromptInputToolbar>
    </PromptInput>
  );
}
