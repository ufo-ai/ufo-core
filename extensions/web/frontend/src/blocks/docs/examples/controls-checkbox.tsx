import { useState } from "react";

import { Checkbox } from "@/blocks/checkbox";

export function ControlsCheckbox() {
  const [notify, setNotify] = useState(false);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <Checkbox label="Unchecked" checked={false} onCheckedChange={() => undefined} />
      <Checkbox label="Checked" checked onCheckedChange={() => undefined} />
      <Checkbox label="Indeterminate" checked={false} indeterminate onCheckedChange={() => undefined} />
      <Checkbox label="Disabled" checked disabled onCheckedChange={() => undefined} />
      <label style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
        <Checkbox label="Notify me before each meeting" checked={notify} onCheckedChange={setNotify} />
        Notify me before each meeting
      </label>
    </div>
  );
}
