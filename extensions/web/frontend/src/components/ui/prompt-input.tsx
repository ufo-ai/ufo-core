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

import { IconChevronDown, IconSparkles } from "@tabler/icons-react";

import { PickedThumbnail } from "@/components/ui/attachment";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { CONTROL, GrowingTextarea } from "@/components/ui/field";
import { BrandMark } from "@/lib/brandMark";
import { cn } from "@/lib/cn";
import {
  AUTO_LABEL,
  AUTO_MODEL,
  modelLabel,
  modelMark,
  modelMenu,
  servesAuto,
} from "@/lib/models";

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

export type EyebrowTone = "default" | "attention";

const EYEBROW_TONES: Record<EyebrowTone, string> = {
  default: "bg-fill-strong text-ink-soft",
  attention: "bg-attention text-attention-ink",
};

/** `attention` is the palette's second accent at the weight a status is tinted at, the one tone that
 *  asks for the member — the same one a late commitment and a stopped run take. */
export function PromptInputEyebrow({
  glyph,
  label,
  action,
  tone = "default",
  onDismiss,
}: {
  glyph?: ReactNode;
  label: string;
  action?: ReactNode;
  tone?: EyebrowTone;
  onDismiss?: () => void;
}) {
  return (
    <div
      role={tone === "attention" ? "status" : undefined}
      className={cn(
        "-mx-lg -mt-lg -mb-2xs flex h-(--size-row) items-center justify-between",
        "rounded-t-bubble pl-2xl pr-2xs text-label",
        EYEBROW_TONES[tone],
      )}
    >
      <span className="flex min-w-0 items-center gap-xs">
        {glyph}
        <span className="truncate">{label}</span>
      </span>
      {action}
      {onDismiss ? (
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
      ) : null}
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
  return <div className="flex items-center justify-between gap-lg">{children}</div>;
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

const MODEL_MARK = "size-(--size-glyph)";

/** The model the chat runs on, chosen beside the send act: a provider per row, and the models it
 *  serves in the flyout beside it. The menu is opened right to left so a flyout stands to the left
 *  of the provider it belongs to — Radix takes a submenu's side from the reading direction and from
 *  nothing else. It also writes that direction onto each popup, which mirrors the rows, so every
 *  popup takes `dir="ltr"` back: the mark leads the label and the chevron or the tick closes the
 *  row, while the flyout still stands to the left.
 *
 *  The menu holds its own open state and shuts it as the pick is made rather than leaving the close
 *  to Radix: a tap on a model in the flyout dismisses the flyout, and the same touch does not always
 *  reach the root, which left the root standing open over an unchanged chip on a phone.
 *
 *  Auto stands as its own row over the providers, because it is the deploy's choice rather than a
 *  model of any one of them, and it is how a member hands the choice back after picking. */
export function PromptInputModel({
  model,
  onPick,
}: {
  model: string;
  onPick: (model: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const groups = modelMenu();
  const auto = servesAuto();
  if (!groups.length && !auto) return null;
  const mark = modelMark(model);
  const pick = (id: string) => {
    setOpen(false);
    onPick(id);
  };
  return (
    <DropdownMenu dir="rtl" open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Button
          variant="quiet"
          aria-label={"Model: " + modelLabel(model)}
          className="gap-sm rounded-full px-md text-label"
        >
          {model === AUTO_MODEL ? (
            <IconSparkles aria-hidden className={MODEL_MARK} stroke={1.5} />
          ) : mark ? (
            <BrandMark provider={mark} className={MODEL_MARK} />
          ) : null}
          <span className="truncate">{modelLabel(model)}</span>
          <IconChevronDown aria-hidden className={cn(MODEL_MARK, "text-ink-soft")} />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent dir="ltr" align="end">
        {auto ? (
          <DropdownMenuRadioGroup value={model} onValueChange={pick}>
            <DropdownMenuRadioItem value={AUTO_MODEL}>
              <span className="flex items-center gap-sm">
                <IconSparkles aria-hidden className={MODEL_MARK} stroke={1.5} />
                {AUTO_LABEL}
              </span>
            </DropdownMenuRadioItem>
          </DropdownMenuRadioGroup>
        ) : null}
        {auto && groups.length ? <DropdownMenuSeparator /> : null}
        {groups.map(({ provider, models }) => (
          <DropdownMenuSub key={provider.id}>
            <DropdownMenuSubTrigger>
              <span className="flex items-center gap-sm">
                <BrandMark provider={provider.mark} className={MODEL_MARK} />
                {provider.label}
              </span>
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent dir="ltr">
              <DropdownMenuRadioGroup value={model} onValueChange={pick}>
                {models.map((choice) => (
                  <DropdownMenuRadioItem key={choice.id} value={choice.id}>
                    <span className="flex items-center gap-sm">
                      <BrandMark provider={provider.mark} className={MODEL_MARK} />
                      {choice.label}
                    </span>
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
            </DropdownMenuSubContent>
          </DropdownMenuSub>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
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
