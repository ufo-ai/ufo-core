import {
  createContext,
  useContext,
  useMemo,
  useRef,
  useState,
  type ClipboardEvent,
  type ComponentProps,
  type DragEvent,
  type ReactNode,
} from "react";

import { Button } from "@/components/ui/button";
import { CONTROL, GrowingTextarea } from "@/components/ui/field";
import { cn } from "@/lib/cn";
import { formatSize } from "@/lib/size";

type Attached = { id: string; file: File };

type Held = {
  attached: Attached[];
  attach: (files: FileList | File[]) => void;
  drop: (id: string) => void;
  clear: () => void;
  choose: () => void;
};

const HELD = createContext<Held | null>(null);

let picked = 0;

/** The files the composer is holding. A message is the words and what is attached to them, so the
 *  parts that show an attachment and the part that sends it read one list. */
function useAttached(): Held {
  const held = useContext(HELD);
  if (held === null) throw new Error("a prompt input part outside its PromptInput");
  return held;
}

const GLYPH = "size-(--size-glyph)";

/** The message box: one card holding what the member is writing, the files they attached to it, and
 *  the acts that send it. A file reaches the card three ways — the attach control, a drop onto the
 *  card, a paste into the box — because a member who has a file in hand does whichever of those
 *  their hands are already doing, and each names the same list. The card holds what it was given
 *  until the send says it took it: a press the composer cannot answer yet — a conversation still
 *  opening, a message with neither words nor files — leaves the attachments where the member put
 *  them rather than dropping them on the way out. */
export function PromptInput({
  onSend,
  className,
  children,
  ...props
}: Omit<ComponentProps<"form">, "onSubmit"> & {
  onSend: (attached: File[]) => boolean;
}) {
  const [attached, setAttached] = useState<Attached[]>([]);
  const picker = useRef<HTMLInputElement>(null);
  const held = useMemo<Held>(
    () => ({
      attached,
      attach: (files) =>
        setAttached((current) => [
          ...current,
          ...Array.from(files).map((file) => ({ id: String(++picked), file })),
        ]),
      drop: (id) => setAttached((current) => current.filter((entry) => entry.id !== id)),
      clear: () => setAttached([]),
      choose: () => picker.current?.click(),
    }),
    [attached],
  );
  const carriesFiles = (event: DragEvent) => event.dataTransfer.types.includes("Files");
  return (
    <HELD.Provider value={held}>
      <form
        {...props}
        onSubmit={(event) => {
          event.preventDefault();
          if (onSend(attached.map((entry) => entry.file))) held.clear();
        }}
        onDragOver={(event) => {
          if (carriesFiles(event)) event.preventDefault();
        }}
        onDrop={(event) => {
          if (!carriesFiles(event)) return;
          event.preventDefault();
          held.attach(event.dataTransfer.files);
        }}
        data-field-card
        className={cn(CONTROL, "flex flex-col gap-sm px-lg py-md", className)}
      >
        <input
          ref={picker}
          type="file"
          multiple
          hidden
          onChange={(event) => {
            held.attach(event.currentTarget.files ?? []);
            event.currentTarget.value = "";
          }}
        />
        {children}
      </form>
    </HELD.Provider>
  );
}

/** What is attached, above the words it will be sent with. Drawn only when the member has attached
 *  something: an empty row is a band of nothing over the box they are writing in. */
export function PromptInputAttachments() {
  const { attached, drop } = useAttached();
  if (!attached.length) return null;
  return (
    <ul role="list" aria-label="Attached files" className="m-0 flex list-none flex-wrap gap-xs p-0">
      {attached.map(({ id, file }) => (
        <li
          key={id}
          className="flex max-w-control-row items-center gap-xs rounded-control border border-edge px-sm py-2xs text-label"
        >
          <span className="truncate">{file.name}</span>
          <span className="tabular-nums text-ink-soft">{formatSize(file.size)}</span>
          <Button
            variant="row"
            aria-label={"Remove " + file.name}
            onClick={() => drop(id)}
            className="border-0 p-0 text-ink-soft hover:bg-transparent hover:text-ink"
          >
            <svg viewBox="0 0 16 16" aria-hidden className={GLYPH}>
              <path
                d="m4.5 4.5 7 7m0-7-7 7"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
            </svg>
          </Button>
        </li>
      ))}
    </ul>
  );
}

/** The box itself, drawn bare: the card around it is the field, and a second surface inside the
 *  first states a box within a box. A file pasted into it is attached rather than typed. */
export function PromptInputTextarea({
  className,
  onPaste,
  ...props
}: ComponentProps<typeof GrowingTextarea>) {
  const { attach } = useAttached();
  return (
    <GrowingTextarea
      {...props}
      bare
      onPaste={(event: ClipboardEvent<HTMLTextAreaElement>) => {
        const files = Array.from(event.clipboardData.files);
        if (files.length) {
          event.preventDefault();
          attach(files);
        }
        onPaste?.(event);
      }}
      className={cn("w-full", className)}
    />
  );
}

/** The acts, under the words they act on: what the member adds to the message on the left, what
 *  sends it on the right. */
export function PromptInputToolbar({ children }: { children: ReactNode }) {
  return <div className="flex items-stretch justify-between gap-sm">{children}</div>;
}

export function PromptInputAttach() {
  const { choose } = useAttached();
  const label = "Attach files";
  return (
    <Button variant="outline" size="icon" aria-label={label} title={label} onClick={choose}>
      <svg viewBox="0 0 16 16" aria-hidden className={GLYPH}>
        <path d="M8 4v8M4 8h8" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    </Button>
  );
}

/** Send and stop are one control, in the one place the eye already goes. Which act it carries is
 *  what the member has to send, never what the turn is doing: words in the box mean send, because
 *  a message sent mid-turn joins that turn, and an empty box under a running turn leaves stopping
 *  as the only act there is. */
export function PromptInputSubmit({
  stops,
  busy,
  disabled,
  onStop,
}: {
  stops: boolean;
  busy?: boolean;
  disabled?: boolean;
  onStop?: () => void;
}) {
  const { attached } = useAttached();
  if (stops && !attached.length)
    return (
      <Button
        variant="outline"
        size="icon"
        aria-label="Stop"
        title="Stop"
        busy={busy}
        onClick={onStop}
      >
        <svg viewBox="0 0 16 16" aria-hidden className={GLYPH}>
          <rect x="4.5" y="4.5" width="7" height="7" rx="1.5" fill="currentColor" />
        </svg>
      </Button>
    );
  return (
    <Button
      type="submit"
      variant="send"
      size="icon"
      aria-label="Send"
      title="Send"
      disabled={disabled}
    >
      <svg viewBox="0 0 16 16" aria-hidden className={GLYPH}>
        <path
          d="M8 12.5V3.5M4 7.5 8 3.5l4 4"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </Button>
  );
}
