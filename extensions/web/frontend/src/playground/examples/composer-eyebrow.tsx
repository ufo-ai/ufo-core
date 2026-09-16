import {
  PromptInput,
  PromptInputAttach,
  PromptInputEyebrow,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputToolbar,
} from "@/components/ui/prompt-input";

export function ComposerEyebrow() {
  return (
    <PromptInput onSend={() => true} className="w-full">
      <PromptInputEyebrow label="Replying to Priya Raman" onDismiss={() => {}} />
      <PromptInputTextarea value="" onChange={() => {}} placeholder="Ask anything" />
      <PromptInputToolbar>
        <PromptInputAttach />
        <PromptInputSubmit stops={false} />
      </PromptInputToolbar>
    </PromptInput>
  );
}
