import { useEffect, useRef } from "react";
import { IconCheck, IconMinus } from "@tabler/icons-react";

import "@/blocks/checkbox.css";

/** A 16px checkbox with an indeterminate state for select-all headers. */
export function Checkbox({
  checked,
  indeterminate = false,
  onCheckedChange,
  label,
  disabled,
}: {
  checked: boolean;
  indeterminate?: boolean;
  onCheckedChange: (checked: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate;
  }, [indeterminate]);
  return (
    <span className="blk-checkbox" data-state={indeterminate ? "indeterminate" : checked ? "checked" : "unchecked"}>
      <input
        ref={ref}
        type="checkbox"
        aria-label={label}
        checked={checked}
        disabled={disabled}
        onChange={(event) => onCheckedChange(event.target.checked)}
      />
      <span className="blk-checkbox-box" aria-hidden="true">
        {indeterminate ? <IconMinus size={12} stroke={2.5} /> : <IconCheck size={12} stroke={2.5} />}
      </span>
    </span>
  );
}
