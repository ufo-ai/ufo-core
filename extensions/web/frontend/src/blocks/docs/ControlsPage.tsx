import { DocPage, DocSection, Example, PropsTable } from "@/blocks/docs/Docs";
import { ControlsAvatar } from "@/blocks/docs/examples/controls-avatar";
import { ControlsAvatarStack } from "@/blocks/docs/examples/controls-avatar-stack";
import { ControlsMenu } from "@/blocks/docs/examples/controls-menu";
import { ControlsMenuButton } from "@/blocks/docs/examples/controls-menu-button";
import { ControlsMenuFocus } from "@/blocks/docs/examples/controls-menu-focus";
import { ControlsCheckbox } from "@/blocks/docs/examples/controls-checkbox";
import avatarSource from "./examples/controls-avatar.tsx?raw";
import avatarStackSource from "./examples/controls-avatar-stack.tsx?raw";
import menuSource from "./examples/controls-menu.tsx?raw";
import menuButtonSource from "./examples/controls-menu-button.tsx?raw";
import menuFocusSource from "./examples/controls-menu-focus.tsx?raw";
import checkboxSource from "./examples/controls-checkbox.tsx?raw";
import { ControlsStates } from "@/blocks/docs/examples/controls-states";
import statesSource from "./examples/controls-states.tsx?raw";
import { ControlsStatesInteractive } from "@/blocks/docs/examples/controls-states-interactive";
import statesInteractiveSource from "./examples/controls-states-interactive.tsx?raw";

export function ControlsPage() {
  return (
    <DocPage
      title="Controls"
      description="Small primitives the other blocks compose: avatars, menus, checkboxes."
    >
      <Example
        title="Avatar sizes"
        description="Three sizes, and a fallback of initials or a gradient when there is no picture."
        code={avatarSource}
        align="start"
      >
        <ControlsAvatar />
      </Example>
      <Example
        title="Avatar stack"
        description="Each face after the first shifts back by half its width and carries the page ground as a ring."
        code={avatarStackSource}
        align="start"
      >
        <ControlsAvatarStack />
      </Example>
      <Example
        title="Dropdown menu"
        description="A label over a run of settings, a rule, then the acts. The destructive one closes the list."
        code={menuSource}
        align="start"
      >
        <ControlsMenu />
      </Example>
      <Example
        title="Menu button"
        description="The drawn trigger: a labelled button carrying the chevron, and the 24px square that holds one mark."
        code={menuButtonSource}
        align="start"
      >
        <ControlsMenuButton />
      </Example>
      <Example
        title="Menu keeping focus"
        description="onCloseAutoFocus hands the caret to the field the choice filled rather than back to the trigger."
        code={menuFocusSource}
        align="start"
      >
        <ControlsMenuFocus />
      </Example>
      <Example
        title="Checkbox states"
        description="Every state of the 16px box, and the label that toggles it when the text is clicked."
        code={checkboxSource}
        align="start"
      >
        <ControlsCheckbox />
      </Example>
      <DocSection
        title="States"
        description="The box in the row it belongs to, at the 16px media slot the item and the table cell both size."
      >
        <Example
          title="Every state"
          description="Unchecked, checked, indeterminate and disabled, each in the media slot of a 34px row."
          code={statesSource}
          align="start"
        >
          <ControlsStates />
        </Example>
        <Example
          title="Interactive states"
          description="The header box reads partial while some rows are picked, and takes or drops all of them."
          code={statesInteractiveSource}
          align="start"
        >
          <ControlsStatesInteractive />
        </Example>
      </DocSection>
      <DocSection title="API">
        <h3>Avatar</h3>
        <PropsTable
          rows={[
            { name: "src", type: "string", description: "The picture. Absent, the fallback or the gradient draws." },
            { name: "alt", type: "string", description: "Who the avatar stands for. It is the accessible name." },
            { name: "fallback", type: "ReactNode", description: "Initials or a mark drawn when there is no picture." },
            { name: "gradient", type: "[string, string]", description: "Two colours for the 135 degree ground." },
            { name: "size", type: "16 | 24 | 32", default: "16", description: "The width and height in pixels." },
          ]}
        />
        <h3>AvatarStack</h3>
        <PropsTable
          rows={[{ name: "children", type: "ReactNode", description: "The avatars, overlapping by half their width." }]}
        />
        <h3>Menu</h3>
        <PropsTable
          rows={[{ name: "children", type: "ReactNode", description: "A MenuTrigger and the MenuContent it opens." }]}
        />
        <h3>MenuTrigger</h3>
        <PropsTable
          rows={[
            {
              name: "data-icon",
              type: '"true"',
              description: "Draws the trigger as a 24px square with no edge, for a mark alone.",
            },
            { name: "children", type: "ReactNode", description: "The label, the mark, or both." },
          ]}
        />
        <h3>MenuButton</h3>
        <PropsTable
          rows={[
            {
              name: "icon",
              type: "boolean",
              default: "false",
              description: "Draws a 24px square holding one mark, and drops the trailing chevron.",
            },
            { name: "label", type: "string", description: "Accessible name. Required when the button holds a mark alone." },
            { name: "children", type: "ReactNode", description: "The label, or the 16px mark for an icon button." },
          ]}
        />
        <h3>MenuContent</h3>
        <PropsTable
          rows={[
            {
              name: "align",
              type: '"start" | "center" | "end"',
              default: '"end"',
              description: "Which edge of the trigger the panel lines up with.",
            },
            {
              name: "onCloseAutoFocus",
              type: "(event: Event) => void",
              description:
                "Fires as the panel closes. Prevent the event to keep the caret where the choice put it instead of returning it to the trigger.",
            },
            { name: "children", type: "ReactNode", description: "Items, checkbox items, labels and separators." },
          ]}
        />
        <h3>MenuItem</h3>
        <PropsTable
          rows={[
            { name: "onSelect", type: "() => void", description: "Fires when the row is chosen; the menu then closes." },
            { name: "disabled", type: "boolean", default: "false", description: "Refuses the choice and dims the row." },
            { name: "destructive", type: "boolean", default: "false", description: "Marks the act that removes something." },
            { name: "children", type: "ReactNode", description: "The row label." },
          ]}
        />
        <h3>MenuCheckboxItem</h3>
        <PropsTable
          rows={[
            { name: "checked", type: "boolean", description: "Whether the setting is on." },
            { name: "onCheckedChange", type: "(checked: boolean) => void", description: "Fires with the state chosen." },
            { name: "children", type: "ReactNode", description: "The row label." },
          ]}
        />
        <h3>MenuLabel</h3>
        <PropsTable rows={[{ name: "children", type: "ReactNode", description: "A heading over a run of rows." }]} />
        <h3>MenuSeparator</h3>
        <PropsTable rows={[{ name: "—", type: "—", description: "A 1px rule between two sections of the menu." }]} />
        <h3>Checkbox</h3>
        <PropsTable
          rows={[
            { name: "checked", type: "boolean", description: "Whether the box is ticked." },
            {
              name: "indeterminate",
              type: "boolean",
              default: "false",
              description: "Draws the dash a select-all header shows over a partial selection.",
            },
            { name: "onCheckedChange", type: "(checked: boolean) => void", description: "Fires with the state chosen." },
            { name: "label", type: "string", description: "The accessible name of the input." },
            { name: "disabled", type: "boolean", default: "false", description: "Refuses the click and dims the box." },
          ]}
        />
      </DocSection>
    </DocPage>
  );
}
