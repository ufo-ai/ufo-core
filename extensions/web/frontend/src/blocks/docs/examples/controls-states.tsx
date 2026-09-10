import { Checkbox } from "@/blocks/checkbox";
import { Item, ItemContent, ItemGroup, ItemMedia, ItemTitle } from "@/blocks/item";

const BOXES = [
  { label: "Unchecked", checked: false, indeterminate: false, disabled: false },
  { label: "Checked", checked: true, indeterminate: false, disabled: false },
  { label: "Indeterminate", checked: false, indeterminate: true, disabled: false },
  { label: "Disabled", checked: false, indeterminate: false, disabled: true },
  { label: "Disabled and checked", checked: true, indeterminate: false, disabled: true },
];

export function ControlsStates() {
  return (
    <ItemGroup>
      {BOXES.map((box) => (
        <Item key={box.label} size="sm" state={box.disabled ? "disabled" : "default"}>
          <ItemMedia variant="checkbox">
            <Checkbox
              label={box.label}
              checked={box.checked}
              indeterminate={box.indeterminate}
              disabled={box.disabled}
              onCheckedChange={() => undefined}
            />
          </ItemMedia>
          <ItemContent>
            <ItemTitle>{box.label}</ItemTitle>
          </ItemContent>
        </Item>
      ))}
    </ItemGroup>
  );
}
