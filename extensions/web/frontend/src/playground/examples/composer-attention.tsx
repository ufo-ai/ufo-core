import {
  PromptInput,
  PromptInputAttach,
  PromptInputEyebrow,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputToolbar,
} from "@/components/ui/prompt-input";

export function ComposerAttention() {
  return (
    <PromptInput onSend={() => true} className="w-full">
      <PromptInputEyebrow label="The run stopped" tone="attention" onDismiss={() => {}} />
      <PromptInputTextarea value="" onChange={() => {}} placeholder="Ask anything" />
      <PromptInputToolbar>
        <PromptInputAttach />
        <PromptInputSubmit stops={false} />
      </PromptInputToolbar>
    </PromptInput>
  );
}
