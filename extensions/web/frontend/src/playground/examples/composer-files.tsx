import { PickedThumbnail } from "@/components/ui/attachment";
import {
  PromptInput,
  PromptInputAttach,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputToolbar,
} from "@/components/ui/prompt-input";

const PICTURE_BYTES = Uint8Array.from(
  atob(
    "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAEUlEQVR4nGOYtbgLK2IYWhIAdlxxwdK0kjwAAAAASUVORK5CYII=",
  ),
  (char) => char.charCodeAt(0),
);

const PICKED = [
  new File([PICTURE_BYTES], "sidebar-after.png", { type: "image/png" }),
  new File(["release 2.14"], "release-note.txt", { type: "text/plain" }),
  new File([""], "support-macros.zip", { type: "application/zip" }),
];

export function ComposerFiles() {
  return (
    <PromptInput onSend={() => true} className="w-full">
      <ul role="list" aria-label="Attached files" className="m-0 flex list-none flex-wrap gap-sm p-0">
        {PICKED.map((file) => (
          <li key={file.name} className="flex">
            <PickedThumbnail file={file} />
          </li>
        ))}
      </ul>
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
