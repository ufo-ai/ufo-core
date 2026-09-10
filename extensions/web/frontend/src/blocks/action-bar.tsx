import { IconChevronDown, IconSearch, IconX } from "@tabler/icons-react";
import {
  useEffect,
  useRef,
  useState,
  type ComponentPropsWithoutRef,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import { Menu, MenuTrigger } from "@/blocks/menu";
import "@/blocks/action-bar.css";

const ICON_SIZE = 16;
const ICON_STROKE = 1.5;

/** The row that heads a lane: a title on the left, actions on the right. */
export function ActionBar({
  variant = "header",
  children,
}: {
  variant?: "header" | "toolbar";
  children: ReactNode;
}) {
  return (
    <div className="blk-action-bar" data-variant={variant}>
      {children}
    </div>
  );
}

/** The lane name, with an optional leading icon. A MenuContent in `menu` makes the whole title its trigger. */
export function ActionBarTitle({
  icon,
  children,
  menu,
}: {
  icon?: ReactNode;
  children: ReactNode;
  menu?: boolean | ReactNode;
}) {
  const chevron = (
    <IconChevronDown className="blk-action-bar-chevron" size={ICON_SIZE} stroke={ICON_STROKE} />
  );
  if (menu !== undefined && typeof menu !== "boolean") {
    return (
      <Menu>
        <MenuTrigger asChild>
          <button type="button" className="blk-action-bar-title" data-menu="true">
            {icon}
            <span className="blk-action-bar-title-text">{children}</span>
            {chevron}
          </button>
        </MenuTrigger>
        {menu}
      </Menu>
    );
  }
  return (
    <div className="blk-action-bar-title">
      {icon}
      <span className="blk-action-bar-title-text">{children}</span>
      {menu ? chevron : null}
    </div>
  );
}

/** The middle slot of an action bar: one line of counts between the title and the actions. */
export function ActionBarCenter({ children }: { children: ReactNode }) {
  return <div className="blk-action-bar-center">{children}</div>;
}

/** The trailing group of an action bar. */
export function ActionBarActions({ children }: { children: ReactNode }) {
  return <div className="blk-action-bar-actions">{children}</div>;
}

/** A 16px icon control with a 24px hit area and a required accessible name. */
export function IconButton({
  label,
  active,
  pressed,
  children,
  ...rest
}: ComponentPropsWithoutRef<"button"> & { label: string; active?: boolean; pressed?: boolean }) {
  return (
    <button
      type="button"
      {...rest}
      className="blk-icon-button"
      aria-label={label}
      aria-pressed={pressed}
      data-active={active ? "true" : undefined}
      data-pressed={pressed ? "true" : undefined}
    >
      {children}
    </button>
  );
}

/** A line of prompt chips that clips at the trailing edge, or wraps to as many lines as it needs. */
export function Prompts({ wrap, children }: { wrap?: boolean; children: ReactNode }) {
  return (
    <div className="blk-prompts" data-wrap={wrap ? "true" : undefined}>
      {children}
    </div>
  );
}

/** A suggested prompt the member sends with one click. `active` marks the filter a row is standing on. */
export function Prompt({
  icon,
  active,
  disabled,
  children,
  onClick,
}: {
  icon?: ReactNode;
  active?: boolean;
  disabled?: boolean;
  children: ReactNode;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      className="blk-prompt"
      data-active={active ? "true" : undefined}
      aria-pressed={active}
      disabled={disabled}
      onClick={onClick}
    >
      {icon}
      <span>{children}</span>
    </button>
  );
}

/** A pill search input sized for a toolbar. `onClear` adds the cross that empties it. */
export function SearchField({
  placeholder = "Search",
  value,
  onChange,
  onClear,
}: {
  placeholder?: string;
  value?: string;
  onChange?: (value: string) => void;
  onClear?: () => void;
}) {
  return (
    <div className="blk-search-field">
      <IconSearch size={ICON_SIZE} stroke={ICON_STROKE} />
      <input
        type="search"
        aria-label={placeholder}
        placeholder={placeholder}
        value={value}
        onChange={(event) => onChange?.(event.target.value)}
      />
      {onClear && value ? (
        <button type="button" className="blk-search-clear" aria-label="Clear" onClick={onClear}>
          <IconX size={ICON_SIZE} stroke={ICON_STROKE} />
        </button>
      ) : null}
    </div>
  );
}

export type ComposerSurface = "muted" | "outline";

const BUSY_LABEL = "Sending";

/** The message field, submitting on Enter: one row holds the icons beside it, two rows under it. */
export function Composer({
  placeholder,
  value,
  onChange,
  onSubmit,
  leading,
  trailing,
  floating,
  disabled,
  busy,
  rows = 1,
  surface = "muted",
  autoFocus,
  focusKey,
}: {
  placeholder: string;
  value?: string;
  onChange?: (value: string) => void;
  onSubmit?: (value: string) => void;
  leading?: ReactNode;
  trailing?: ReactNode;
  floating?: boolean;
  disabled?: boolean;
  busy?: boolean;
  rows?: 1 | 2;
  surface?: ComposerSurface;
  autoFocus?: boolean;
  focusKey?: unknown;
}) {
  const [draft, setDraft] = useState("");
  const field = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);
  const hold = (node: HTMLInputElement | HTMLTextAreaElement | null) => {
    field.current = node;
  };
  const seen = useRef(focusKey);
  useEffect(() => {
    if (focusKey === seen.current) return;
    seen.current = focusKey;
    field.current?.focus();
  }, [focusKey]);
  const text = value ?? draft;
  const shut = disabled === true || busy === true;
  const change = (next: string) => {
    setDraft(next);
    onChange?.(next);
  };
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (shut) return;
    onSubmit?.(text);
  };
  const enter = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    if (shut) return;
    onSubmit?.(text);
  };
  const tail = busy ? <span className="blk-composer-busy">{BUSY_LABEL}</span> : trailing;
  return (
    <form
      className="blk-composer"
      data-rows={rows}
      data-surface={surface}
      data-floating={floating ? "true" : undefined}
      data-disabled={disabled ? "true" : undefined}
      data-busy={busy ? "true" : undefined}
      onSubmit={submit}
    >
      {rows === 2 ? (
        <>
          <textarea
            ref={hold}
            rows={1}
            autoFocus={autoFocus}
            aria-label={placeholder}
            placeholder={placeholder}
            value={text}
            disabled={shut}
            onChange={(event) => change(event.target.value)}
            onKeyDown={enter}
          />
          <div className="blk-composer-controls">
            <span>{leading}</span>
            <span>{tail}</span>
          </div>
        </>
      ) : (
        <>
          {leading}
          <input
            ref={hold}
            autoFocus={autoFocus}
            aria-label={placeholder}
            placeholder={placeholder}
            value={text}
            disabled={shut}
            onChange={(event) => change(event.target.value)}
          />
          {tail}
        </>
      )}
    </form>
  );
}
