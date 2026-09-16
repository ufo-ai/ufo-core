import {
  Attachment,
  AttachmentBadge,
  AttachmentContent,
  AttachmentDescription,
  AttachmentGroup,
  AttachmentTitle,
} from "@/components/ui/attachment";
import {
  PromptInput,
  PromptInputAttach,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputToolbar,
} from "@/components/ui/prompt-input";

const PICKED = [
  { name: "release-note-2.14.pdf", weight: "218 KB", kind: "PDF" },
  { name: "support-macros.xlsx", weight: "64 KB", kind: "XLSX" },
  { name: "migration-plan.docx", weight: "44 KB", kind: "DOCX" },
];

export function ComposerFiles() {
  return (
    <PromptInput onSend={() => true} className="w-full">
      <AttachmentGroup>
        {PICKED.map(({ name, weight, kind }) => (
          <Attachment key={name}>
            <AttachmentContent>
              <AttachmentTitle>{name}</AttachmentTitle>
              <AttachmentDescription>{weight}</AttachmentDescription>
            </AttachmentContent>
            <AttachmentBadge>{kind}</AttachmentBadge>
          </Attachment>
        ))}
      </AttachmentGroup>
      <PromptInputTextarea
        value="Here is the draft and the two files support asked for."
        onChange={() => {}}
        placeholder="Ask anything"
      />
      <PromptInputToolbar>
        <PromptInputAttach />
        <PromptInputSubmit stops={false} />
      </PromptInputToolbar>
    </PromptInput>
  );
}
