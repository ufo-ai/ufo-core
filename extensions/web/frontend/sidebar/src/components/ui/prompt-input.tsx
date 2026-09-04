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

import { PickedThumbnail } from "@/components/ui/attachment";
import { Button } from "@/components/ui/button";
import { CONTROL, GrowingTextarea } from "@/components/ui/field";
import { cn } from "@/lib/cn";

type Attached = { id: string; file: File };

/** How many files one message carries. The surface refuses an eleventh (`MAX_INBOUND_FILES` in
 *  `ufo_ext_web.surface`), and a refusal after the member let go of the files is a message they have
 *  to build again — so the card holds the same bound where they can still see it. */
export const MAX_ATTACHED = 10;

type Held = {
  attached: Attached[];
  full: boolean;
  attach: (files: FileList | File[]) => void;
  drop: (id: string) => void;
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
 *  the acts that send it. Its contents keep the same inset on every edge, and the words stand clear
 *  of the controls under them. It is a surface rather than an outline — the fill is what separates it
 *  from the pane, and a stroke around a box the member is already looking into says nothing the
 *  fill has not. A file reaches the card three ways — the attach control, a drop onto
 *  the card, a paste into the box — because a member who has a file in hand does whichever of those
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
  onSend: (attached: File[]) => boolean | Promise<boolean>;
}) {
  const [attached, setAttached] = useState<Attached[]>([]);
  const [full, setFull] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  // A send carries its files to the store before it admits anything, so the card still holds them
  // while it runs. A second press in that window would send the same files again, and on the start
  // screen would open a second conversation, so one send at a time is what the card allows.
  const sending = useRef(false);
  const held = useMemo<Held>(
    () => ({
      attached,
      full,
      attach: (files) => {
        const picking = Array.from(files);
        const room = MAX_ATTACHED - attached.length;
        setFull(picking.length > room);
        setAttached((current) => [
          ...current,
          ...picking.slice(0, room).map((file) => ({ id: String(++picked), file })),
        ]);
      },
      drop: (id) => {
        setFull(false);
        setAttached((current) => current.filter((entry) => entry.id !== id));
      },
      choose: () => picker.current?.click(),
    }),
    [attached, full],
  );
  const carriesFiles = (event: DragEvent) => event.dataTransfer.types.includes("Files");
  return (
    <HELD.Provider value={held}>
      <form
        {...props}
        onSubmit={async (event) => {
          event.preventDefault();
          if (sending.current) return;
          sending.current = true;
          // Only what this send took leaves the card. A file the member attaches while the upload
          // runs was never in this body, so clearing the whole list would drop it unsent.
          const sent = attached;
          try {
            if (await onSend(sent.map((entry) => entry.file))) {
              setFull(false);
              setAttached((current) => current.filter((entry) => !sent.includes(entry)));
            }
          } finally {
            sending.current = false;
          }
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
        className={cn(CONTROL, "flex flex-col gap-5xl rounded-bubble border-0 p-lg", className)}
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

/** The application the message addresses, drawn as a distinct band across the card's top edge so it
 *  qualifies the words without becoming part of their field. It wears the mark the sidebar draws
 *  that app under, so the name in the composer and the row the member opened it from read as one
 *  thing. A stand-in agent the portal holds no mark for is named without one rather than under a
 *  hole.
 *
 *  The band is a child of the card, so it undoes the card to reach the card's edges: it pulls out
 *  by the inset on three sides and hands back most of the row the card sets between its children,
 *  leaving the words one inset under it rather than a whole row. Its own right inset is the
 *  difference between that inset and the padding the dismiss control already carries, so the glyph
 *  lands on the same edge the words on the other side start from. */
export function PromptInputEyebrow({
  glyph,
  label,
  onDismiss,
}: {
  glyph?: ReactNode;
  label: string;
  onDismiss: () => void;
}) {
  return (
    <div className="-mx-lg -mt-lg -mb-2xs flex h-(--size-row) items-center justify-between rounded-t-bubble bg-fill-strong pl-2xl pr-2xs text-label text-ink-soft">
      <span className="flex min-w-0 items-center gap-xs">
        {glyph}
        <span className="truncate">{label}</span>
      </span>
      <Button variant="quiet" aria-label={"Stop addressing " + label} onClick={onDismiss}>
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
    </div>
  );
}

/** What is attached, above the words it will be sent with — each file drawn as the picture it is,
 *  so the member reads what they picked rather than a filename they have to trust. Drawn only when
 *  the member has attached something: an empty row is a band of nothing over the box they are
 *  writing in. */
export function PromptInputAttachments() {
  const { attached, full, drop } = useAttached();
  if (!attached.length) return null;
  return (
    <>
      {full && (
        <p className="m-0 text-small text-ink-soft">
          A message carries at most {MAX_ATTACHED} files.
        </p>
      )}
      <ul
        role="list"
        aria-label="Attached files"
        className="m-0 flex list-none flex-wrap gap-sm p-0"
      >
        {attached.map(({ id, file }) => (
          <li key={id} className="flex">
            <PickedThumbnail file={file}>
              <Button
                variant="row"
                aria-label={"Remove " + file.name}
                onClick={() => drop(id)}
                className="absolute top-0 right-0 m-xs border-edge bg-card p-2xs text-ink-soft hover:text-ink"
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
            </PickedThumbnail>
          </li>
        ))}
      </ul>
    </>
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
      className={cn("w-full max-narrow:min-h-(--size-control)", className)}
    />
  );
}

/** The acts, under the words they act on: what the member adds to the message on the left, what
 *  sends it on the right. */
export function PromptInputToolbar({ children }: { children: ReactNode }) {
  return <div className="flex items-stretch justify-between gap-lg">{children}</div>;
}

export function PromptInputAttach() {
  const { choose } = useAttached();
  const label = "Attach files";
  return (
    <Button variant="mark" size="glyph" aria-label={label} title={label} onClick={choose}>
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
