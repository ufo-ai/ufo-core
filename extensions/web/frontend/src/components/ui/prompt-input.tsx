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

/** The surface refuses an eleventh (`MAX_INBOUND_FILES` in `ufo_ext_web.surface`), so the card holds
 *  the same bound where the member can still see it. */
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

function useAttached(): Held {
  const held = useContext(HELD);
  if (held === null) throw new Error("a prompt input part outside its PromptInput");
  return held;
}

const GLYPH = "size-(--size-glyph)";

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
          // Only what this send took leaves the card: a file attached while the upload runs was never in this
          // body, so clearing the whole list would drop it unsent.
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
