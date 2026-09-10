import type { ReactNode } from "react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { IconCheck, IconChevronDown } from "@tabler/icons-react";

import "@/blocks/menu.css";

const ICON_SIZE = 16;
const ICON_STROKE = 1.5;

/** The root of a dropdown menu: a trigger and the content it opens. */
export const Menu = DropdownMenu.Root;

/** The control that opens the menu. `data-icon="true"` draws it as a square icon button. */
export const MenuTrigger = DropdownMenu.Trigger;

/** The drawn trigger: a labelled button carrying the chevron, or a square holding one mark. */
export function MenuButton({
  children,
  icon,
  label,
}: {
  children?: ReactNode;
  icon?: boolean;
  label?: string;
}) {
  return (
    <DropdownMenu.Trigger asChild>
      <button
        type="button"
        className="blk-menu-button"
        data-icon={icon ? "true" : undefined}
        aria-label={label}
      >
        {children}
        {icon ? null : <IconChevronDown size={ICON_SIZE} stroke={ICON_STROKE} />}
      </button>
    </DropdownMenu.Trigger>
  );
}

/** A dropdown menu panel anchored to its trigger. */
export function MenuContent({
  align = "end",
  onCloseAutoFocus,
  children,
}: {
  align?: "start" | "center" | "end";
  onCloseAutoFocus?: (event: Event) => void;
  children: ReactNode;
}) {
  return (
    <DropdownMenu.Portal>
      <DropdownMenu.Content
        className="blk-menu"
        align={align}
        sideOffset={4}
        onCloseAutoFocus={onCloseAutoFocus}
      >
        {children}
      </DropdownMenu.Content>
    </DropdownMenu.Portal>
  );
}

/** One act in the menu; `destructive` marks the one that removes something. */
export function MenuItem({
  onSelect,
  disabled,
  destructive,
  children,
}: {
  onSelect?: () => void;
  disabled?: boolean;
  destructive?: boolean;
  children: ReactNode;
}) {
  return (
    <DropdownMenu.Item
      className="blk-menu-item"
      data-destructive={destructive ? "true" : undefined}
      disabled={disabled}
      onSelect={onSelect}
    >
      {children}
    </DropdownMenu.Item>
  );
}

/** A menu row that reports and toggles a setting. */
export function MenuCheckboxItem({
  checked,
  onCheckedChange,
  children,
}: {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  children: ReactNode;
}) {
  return (
    <DropdownMenu.CheckboxItem
      className="blk-menu-item"
      checked={checked}
      onCheckedChange={(next) => onCheckedChange(next === true)}
    >
      <span className="blk-menu-check">
        <DropdownMenu.ItemIndicator>
          <IconCheck size={12} stroke={2} />
        </DropdownMenu.ItemIndicator>
      </span>
      {children}
    </DropdownMenu.CheckboxItem>
  );
}

/** A heading over a run of menu rows. */
export function MenuLabel({ children }: { children: ReactNode }) {
  return <DropdownMenu.Label className="blk-menu-label">{children}</DropdownMenu.Label>;
}

/** A rule that breaks the menu into sections. */
export function MenuSeparator() {
  return <DropdownMenu.Separator className="blk-menu-separator" />;
}
