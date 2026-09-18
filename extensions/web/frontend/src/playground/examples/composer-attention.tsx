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
      <PromptInputEyebrow label="No credit left in this workspace." />
      <PromptInputTextarea value="" onChange={() => {}} placeholder="Ask anything" />
      <PromptInputToolbar>
        <PromptInputAttach />
        <PromptInputSubmit stops={false} />
      </PromptInputToolbar>
    </PromptInput>
  );
}
