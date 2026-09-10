import { DocPage, DocSection, Example, PropsTable } from "@/blocks/docs/Docs";
import { ItemDefault } from "@/blocks/docs/examples/item-default";
import { ItemVariants } from "@/blocks/docs/examples/item-variants";
import { ItemSmall } from "@/blocks/docs/examples/item-sm";
import { ItemAvatar } from "@/blocks/docs/examples/item-avatar";
import { ItemImage } from "@/blocks/docs/examples/item-image";
import { ItemGroupExample } from "@/blocks/docs/examples/item-group";
import { ItemHeaderExample } from "@/blocks/docs/examples/item-header";
import { ItemLink } from "@/blocks/docs/examples/item-link";
import { ItemDropdown } from "@/blocks/docs/examples/item-dropdown";
import { ItemExtraSmall } from "@/blocks/docs/examples/item-xs";
import { ItemNarrow } from "@/blocks/docs/examples/item-narrow";
import { ItemMeeting } from "@/blocks/docs/examples/item-meeting";
import { ItemCollapsible } from "@/blocks/docs/examples/item-collapsible";
import { ItemInteractive } from "@/blocks/docs/examples/item-interactive";
import { ItemDisabled } from "@/blocks/docs/examples/item-disabled";
import { ItemSeparators } from "@/blocks/docs/examples/item-separators";
import { ItemMono } from "@/blocks/docs/examples/item-mono";
import { ItemStates } from "@/blocks/docs/examples/item-states";
import { ItemSectionExample } from "@/blocks/docs/examples/item-section";
import { ItemStatesInteractive } from "@/blocks/docs/examples/item-states-interactive";
import { ItemCheckboxRow } from "@/blocks/docs/examples/item-checkbox-row";
import { ItemMediaMono } from "@/blocks/docs/examples/item-media-mono";
import { ItemSectionEmpty } from "@/blocks/docs/examples/item-section-empty";
import { ItemTagToggle } from "@/blocks/docs/examples/item-tag-toggle";
import defaultSource from "./examples/item-default.tsx?raw";
import variantsSource from "./examples/item-variants.tsx?raw";
import smallSource from "./examples/item-sm.tsx?raw";
import avatarSource from "./examples/item-avatar.tsx?raw";
import imageSource from "./examples/item-image.tsx?raw";
import groupSource from "./examples/item-group.tsx?raw";
import headerSource from "./examples/item-header.tsx?raw";
import linkSource from "./examples/item-link.tsx?raw";
import dropdownSource from "./examples/item-dropdown.tsx?raw";
import extraSmallSource from "./examples/item-xs.tsx?raw";
import narrowSource from "./examples/item-narrow.tsx?raw";
import meetingSource from "./examples/item-meeting.tsx?raw";
import collapsibleSource from "./examples/item-collapsible.tsx?raw";
import interactiveSource from "./examples/item-interactive.tsx?raw";
import disabledSource from "./examples/item-disabled.tsx?raw";
import separatorsSource from "./examples/item-separators.tsx?raw";
import monoSource from "./examples/item-mono.tsx?raw";
import statesSource from "./examples/item-states.tsx?raw";
import sectionSource from "./examples/item-section.tsx?raw";
import statesInteractiveSource from "./examples/item-states-interactive.tsx?raw";
import checkboxRowSource from "./examples/item-checkbox-row.tsx?raw";
import mediaMonoSource from "./examples/item-media-mono.tsx?raw";
import sectionEmptySource from "./examples/item-section-empty.tsx?raw";
import tagToggleSource from "./examples/item-tag-toggle.tsx?raw";

export function ItemPage() {
  return (
    <DocPage
      title="Item"
      description="A flexible row for lists: media, content, meta and actions, with optional accent and status."
    >
      <Example title="Default" code={defaultSource} align="start">
        <ItemDefault />
      </Example>
      <Example
        title="Variants"
        description="A rule separates plain rows; outline and muted close their own edge."
        code={variantsSource}
        align="start"
      >
        <ItemVariants />
      </Example>
      <Example
        title="Size sm"
        description="The 34px row a task list runs on: two marks, a title that takes the slack, then the trailing facts."
        code={smallSource}
        align="start"
      >
        <ItemSmall />
      </Example>
      <Example
        title="Avatar"
        description="The media slot sizes the face; a stack of them reads as the people on the record."
        code={avatarSource}
        align="start"
      >
        <ItemAvatar />
      </Example>
      <Example
        title="Image"
        description="A description runs to the line count it is given, then takes the ellipsis."
        code={imageSource}
        align="start"
      >
        <ItemImage />
      </Example>
      <Example
        title="Group"
        description="One column of rows under one rule each. Flush drops the rule under the last row."
        code={groupSource}
        align="start"
      >
        <ItemGroupExample />
      </Example>
      <Example
        title="Header"
        description="A header and a footer wrap to their own lines, so a picture can sit between them."
        code={headerSource}
        align="start"
      >
        <ItemHeaderExample />
      </Example>
      <Example
        title="Link"
        description="Render gives the row an element of its own, so a whole row can be one link."
        code={linkSource}
        align="start"
      >
        <ItemLink />
      </Example>
      <Example
        title="Dropdown"
        description="The trailing slot holds the menu that acts on the row."
        code={dropdownSource}
        align="start"
      >
        <ItemDropdown />
      </Example>
      <Example
        title="Extra small"
        description="The 28px row a navigation list runs on, at the 12px title step."
        code={extraSmallSource}
        align="start"
      >
        <ItemExtraSmall />
      </Example>
      <Example
        title="Narrow"
        description="At a lane's 349px the description takes the ellipsis rather than pushing the count to a second line."
        code={narrowSource}
        align="start"
      >
        <ItemNarrow />
      </Example>
      <Example
        title="Meeting"
        description="Upcoming rows carry the primary bar; a past day drops to the secondary ink and the inactive bar."
        code={meetingSource}
        align="start"
      >
        <ItemMeeting />
      </Example>
      <Example
        title="Collapsible header"
        description="The chevron folds the rows the header stands over."
        code={collapsibleSource}
        align="start"
      >
        <ItemCollapsible />
      </Example>
      <Example
        title="Interactive"
        description="An onClick row is a button: it takes focus, hovers to the muted ground, and holds the active state."
        code={interactiveSource}
        align="start"
      >
        <ItemInteractive />
      </Example>
      <Example
        title="Checkbox in a clickable row"
        description="The row opens the record and the box marks it done: the box swallows its own click, so neither act fires the other."
        code={checkboxRowSource}
        align="start"
      >
        <ItemCheckboxRow />
      </Example>
      <Example title="Disabled" code={disabledSource} align="start">
        <ItemDisabled />
      </Example>
      <Example title="Group with separators" code={separatorsSource} align="start">
        <ItemSeparators />
      </Example>
      <Example
        title="Tag as a filter"
        description="An onClick tag is a button reporting aria-pressed, so the collection a row names is also the filter."
        code={tagToggleSource}
        align="start"
      >
        <ItemTagToggle />
      </Example>
      <Example
        title="Media in mono"
        description="The leading slot takes the mono face like the title and the meta, for an identifier before the text."
        code={mediaMonoSource}
        align="start"
      >
        <ItemMediaMono />
      </Example>
      <Example
        title="Mono lines"
        description="A channel name, a detail line and an issue number set in the mono face at the same size."
        code={monoSource}
        align="start"
      >
        <ItemMono />
      </Example>
      <DocSection
        title="States"
        description="The row carries the state; the app decides when it applies."
      >
        <Example
          title="Every state"
          description="Selected, dragging, editing, done, past and disabled, each with the grip, the box and the circle."
          code={statesSource}
          align="start"
        >
          <ItemStates />
        </Example>
        <Example
          title="Section"
          description="The section draws its own heading and folds the rows under it, so an app hands over a label and its rows."
          code={sectionSource}
          align="start"
        >
          <ItemSectionExample />
        </Example>
        <Example
          title="Empty section"
          description="A section with no rows draws its empty line in place of them, and drops it as soon as a row arrives."
          code={sectionEmptySource}
          align="start"
        >
          <ItemSectionEmpty />
        </Example>
        <Example
          title="Interactive states"
          description="The box selects, the circle moves the row on, the pencil edits the title, and the grip reorders the group."
          code={statesInteractiveSource}
          align="start"
        >
          <ItemStatesInteractive />
        </Example>
      </DocSection>
      <DocSection title="API">
        <h3>Item</h3>
        <PropsTable
          rows={[
            {
              name: "variant",
              type: '"default" | "outline" | "muted"',
              default: '"default"',
              description: "A row under a rule, a bordered card, or a filled tile.",
            },
            {
              name: "size",
              type: '"default" | "sm" | "xs"',
              default: '"default"',
              description: "The 83px meeting row, the 34px single-line row, or the 28px navigation row.",
            },
            {
              name: "accent",
              type: '"primary" | "muted" | "none"',
              default: '"none"',
              description: "Draws the 1.5px bar down the leading edge.",
            },
            {
              name: "state",
              type: '"default" | "active" | "disabled" | "past"',
              default: '"default"',
              description: "Selected, unavailable, or elapsed. Past drops the title to the secondary ink.",
            },
            {
              name: "selected",
              type: "boolean",
              default: "false",
              description: "Fills the row and draws the 1.5px primary bar down its leading edge.",
            },
            {
              name: "dragging",
              type: "boolean",
              default: "false",
              description: "Fades the row to .6, fills it and lifts it on the float shadow while it is held.",
            },
            {
              name: "editing",
              type: "boolean",
              default: "false",
              description: "Fills the row and lets its title and description run past one line.",
            },
            {
              name: "dragHandle",
              type: "boolean",
              default: "false",
              description: "Draws the 16px grip before the content, at the grab cursor.",
            },
            {
              name: "render",
              type: "ReactElement",
              description: "The element the row is drawn as, such as an anchor. It takes the row's class and state.",
            },
            {
              name: "onClick",
              type: "() => void",
              description:
                "Renders the row as a div at role button: it hovers, takes focus and fires on Enter and Space, so a checkbox or a menu inside it stays valid.",
            },
          ]}
        />
        <h3>ItemGroup</h3>
        <PropsTable
          rows={[
            {
              name: "flush",
              type: "boolean",
              default: "false",
              description: "Drops the rule under the last row, for a group that closes a card.",
            },
            {
              name: "onReorder",
              type: "(from: number, to: number) => void",
              description:
                "Makes the Item children draggable and reports the seat one was dropped into, counted over the items alone.",
            },
            {
              name: "children",
              type: "ReactNode",
              description: "Items, headers and separators stacked in one column.",
            },
          ]}
        />
        <h3>ItemSeparator</h3>
        <PropsTable rows={[{ name: "—", type: "—", description: "A 1px rule that breaks a group into sections." }]} />
        <h3>ItemMedia</h3>
        <PropsTable
          rows={[
            {
              name: "variant",
              type: '"default" | "icon" | "image" | "avatar" | "status" | "checkbox"',
              default: '"icon"',
              description:
                "A plain slot, a 16px mark, a 40px picture, a 24px face, the progress circle, or a 16px box. The box slot stops its clicks reaching a clickable row.",
            },
            { name: "font", type: '"sans" | "mono"', default: '"sans"', description: "The face of the text in the slot." },
            { name: "children", type: "ReactNode", description: "The icon, avatar or image element." },
          ]}
        />
        <h3>ItemContent</h3>
        <PropsTable
          rows={[{ name: "children", type: "ReactNode", description: "The text column, which takes the free width." }]}
        />
        <h3>ItemTitle</h3>
        <PropsTable
          rows={[
            {
              name: "font",
              type: '"sans" | "mono"',
              default: '"sans"',
              description: "Sets the line in the mono face at the same size.",
            },
            {
              name: "editable",
              type: "{ value: string; onChange(v: string): void; onCommit(): void; onCancel(): void }",
              description:
                "Swaps the line for a field set like it, which commits on Enter or blur and cancels on Escape.",
            },
            { name: "children", type: "ReactNode", description: "The first line, 13px medium, cut with an ellipsis." },
          ]}
        />
        <h3>ItemDescription</h3>
        <PropsTable
          rows={[
            {
              name: "lines",
              type: "1 | 2 | 3",
              default: "1",
              description: "How many lines the text runs to before the ellipsis.",
            },
            {
              name: "font",
              type: '"sans" | "mono"',
              default: '"sans"',
              description: "Sets the line in the mono face at the same size.",
            },
            { name: "children", type: "ReactNode", description: "The secondary line." },
          ]}
        />
        <h3>ItemMeta</h3>
        <PropsTable
          rows={[
            {
              name: "font",
              type: '"sans" | "mono"',
              default: '"sans"',
              description: "Sets the line in the mono face at the same size.",
            },
            { name: "children", type: "ReactNode", description: "An 11px line such as a time, a place, or a date." },
          ]}
        />
        <h3>ItemActions</h3>
        <PropsTable
          rows={[{ name: "children", type: "ReactNode", description: "Trailing tags, counts and dates, held to the right edge." }]}
        />
        <h3>ItemHeader</h3>
        <PropsTable
          rows={[
            { name: "badge", type: "ReactNode", description: "The 24px square before the label, such as a date number." },
            {
              name: "accent",
              type: '"primary" | "muted"',
              default: '"muted"',
              description: "Primary fills the badge and lifts the label to the first ink.",
            },
            {
              name: "collapsible",
              type: "boolean",
              default: "false",
              description: "Draws the chevron that reports and toggles the open state.",
            },
            { name: "open", type: "boolean", description: "Controls the open state; omit it to let the header hold its own." },
            { name: "onOpenChange", type: "(open: boolean) => void", description: "Fires with the state the chevron moved to." },
            { name: "children", type: "ReactNode", description: "The label." },
          ]}
        />
        <h3>ItemSection</h3>
        <PropsTable
          rows={[
            { name: "label", type: "ReactNode", description: "The heading over the rows." },
            { name: "badge", type: "ReactNode", description: "The 24px square before the label, such as a date number." },
            { name: "count", type: "number", description: "Drawn as a Tag after the label." },
            { name: "accent", type: '"primary" | "muted"', default: '"muted"', description: "Primary fills the badge and lifts the label to the first ink." },
            { name: "collapsible", type: "boolean", default: "true", description: "Draws the chevron. False holds the rows open and drops it." },
            { name: "open", type: "boolean", description: "Controls the fold; omit it to let the section hold its own." },
            { name: "defaultOpen", type: "boolean", default: "true", description: "The state an uncontrolled section starts in." },
            { name: "onOpenChange", type: "(open: boolean) => void", description: "Fires with the state the chevron moved to." },
            {
              name: "empty",
              type: "ReactNode",
              description: "One secondary line drawn under the heading while the section holds no rows.",
            },
            { name: "children", type: "ReactNode", description: "The rows the heading folds." },
          ]}
        />
        <h3>ItemFooter</h3>
        <PropsTable
          rows={[{ name: "children", type: "ReactNode", description: "A full-width row that wraps under the content." }]}
        />
        <h3>Tag</h3>
        <PropsTable
          rows={[
            {
              name: "onClick",
              type: "() => void",
              description: "Draws the pill as a button reporting aria-pressed, for a tag that filters the list.",
            },
            {
              name: "pressed",
              type: "boolean",
              default: "false",
              description: "Holds the button filled at the chip ground and lifts its label to the first ink.",
            },
            { name: "children", type: "ReactNode", description: "The pill label." },
          ]}
        />
        <h3>Count</h3>
        <PropsTable
          rows={[
            { name: "icon", type: "ReactNode", description: "A 14px mark before the number." },
            { name: "children", type: "ReactNode", description: "The number." },
          ]}
        />
        <h3>StatusIcon</h3>
        <PropsTable
          rows={[
            {
              name: "status",
              type: '"ready" | "started" | "working" | "done"',
              description: "The circle a row shows: dotted, open, half, or filled.",
            },
            {
              name: "onClick",
              type: "() => void",
              description: "Draws the circle as a button that reports pressed on done, and hovers to the muted ground.",
            },
            { name: "label", type: "string", description: "The button's accessible name. It falls back to the status." },
          ]}
        />
      </DocSection>
    </DocPage>
  );
}
