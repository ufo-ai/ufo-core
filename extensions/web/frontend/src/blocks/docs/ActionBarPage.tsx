import { DocPage, DocSection, Example, PropsTable } from "@/blocks/docs/Docs";
import { ComposerExample } from "@/blocks/docs/examples/action-bar-composer";
import composerSource from "@/blocks/docs/examples/action-bar-composer.tsx?raw";
import { ComposerFloatingExample } from "@/blocks/docs/examples/action-bar-composer-floating";
import composerFloatingSource from "@/blocks/docs/examples/action-bar-composer-floating.tsx?raw";
import { ComposerOutlineExample } from "@/blocks/docs/examples/action-bar-composer-outline";
import composerOutlineSource from "@/blocks/docs/examples/action-bar-composer-outline.tsx?raw";
import { ComposerRowsExample } from "@/blocks/docs/examples/action-bar-composer-rows";
import composerRowsSource from "@/blocks/docs/examples/action-bar-composer-rows.tsx?raw";
import { ComposerFocusExample } from "@/blocks/docs/examples/action-bar-composer-focus";
import composerFocusSource from "@/blocks/docs/examples/action-bar-composer-focus.tsx?raw";
import { ActionBarTitleMenu } from "@/blocks/docs/examples/action-bar-title-menu";
import titleMenuSource from "@/blocks/docs/examples/action-bar-title-menu.tsx?raw";
import { HeaderExample } from "@/blocks/docs/examples/action-bar-header";
import headerSource from "@/blocks/docs/examples/action-bar-header.tsx?raw";
import { HeaderActionsExample } from "@/blocks/docs/examples/action-bar-header-actions";
import headerActionsSource from "@/blocks/docs/examples/action-bar-header-actions.tsx?raw";
import { HeaderCenterExample } from "@/blocks/docs/examples/action-bar-header-center";
import headerCenterSource from "@/blocks/docs/examples/action-bar-header-center.tsx?raw";
import { IconButtonStatesExample } from "@/blocks/docs/examples/action-bar-icon-buttons";
import iconButtonsSource from "@/blocks/docs/examples/action-bar-icon-buttons.tsx?raw";
import { PromptsExample } from "@/blocks/docs/examples/action-bar-prompts";
import promptsSource from "@/blocks/docs/examples/action-bar-prompts.tsx?raw";
import { PromptsActiveExample } from "@/blocks/docs/examples/action-bar-prompts-active";
import promptsActiveSource from "@/blocks/docs/examples/action-bar-prompts-active.tsx?raw";
import { PromptsOverflowExample } from "@/blocks/docs/examples/action-bar-prompts-overflow";
import promptsOverflowSource from "@/blocks/docs/examples/action-bar-prompts-overflow.tsx?raw";
import { PromptsFooterExample } from "@/blocks/docs/examples/action-bar-prompts-footer";
import promptsFooterSource from "@/blocks/docs/examples/action-bar-prompts-footer.tsx?raw";
import { ToolbarExample } from "@/blocks/docs/examples/action-bar-toolbar";
import toolbarSource from "@/blocks/docs/examples/action-bar-toolbar.tsx?raw";
import { ActionBarStates } from "@/blocks/docs/examples/action-bar-states";
import statesSource from "@/blocks/docs/examples/action-bar-states.tsx?raw";
import { ActionBarStatesInteractive } from "@/blocks/docs/examples/action-bar-states-interactive";
import statesInteractiveSource from "@/blocks/docs/examples/action-bar-states-interactive.tsx?raw";

export function ActionBarPage() {
  return (
    <DocPage title="Action Bar" description="Lane headers, prompt suggestions, toolbars and the composer.">
      <Example title="Header" code={headerSource} align="start">
        <HeaderExample />
      </Example>
      <Example
        title="Header with actions"
        description="Every action is an icon button with an accessible name."
        code={headerActionsSource}
        align="start"
      >
        <HeaderActionsExample />
      </Example>
      <Example
        title="Title menu"
        description="A MenuContent in menu makes the whole title the trigger, so the lane name opens the lane's settings."
        code={titleMenuSource}
        align="start"
      >
        <ActionBarTitleMenu />
      </Example>
      <Example title="Prompts" code={promptsSource} align="start">
        <PromptsExample />
      </Example>
      <Example
        title="Filter chips"
        description="The chip the row is standing on is filled and takes the first ink."
        code={promptsActiveSource}
        align="start"
      >
        <PromptsActiveExample />
      </Example>
      <Example
        title="Prompts overflow"
        description="The row stays on one line and fades at the trailing edge."
        code={promptsOverflowSource}
        align="start"
      >
        <PromptsOverflowExample />
      </Example>
      <Example
        title="Prompts in a card footer"
        description="The row takes the whole footer, so the trailing fade covers empty track and never a chip."
        code={promptsFooterSource}
        align="start"
      >
        <PromptsFooterExample />
      </Example>
      <Example
        title="Header with a centre line"
        description="The counts take the middle of the row; the title stops taking the free width."
        code={headerCenterSource}
        align="start"
      >
        <HeaderCenterExample />
      </Example>
      <Example title="Toolbar" code={toolbarSource} align="start">
        <ToolbarExample />
      </Example>
      <Example title="Composer" code={composerSource} align="start">
        <ComposerExample />
      </Example>
      <Example
        title="Composer two rows"
        description="The placeholder takes the first line; the icons sit under it. The field grows to three lines."
        code={composerRowsSource}
        align="start"
      >
        <ComposerRowsExample />
      </Example>
      <Example
        title="Composer outline"
        description="On a muted ground the muted composer disappears, so the outline surface draws its own edge."
        code={composerOutlineSource}
        align="start"
      >
        <ComposerOutlineExample />
      </Example>
      <Example title="Composer floating" code={composerFloatingSource}>
        <ComposerFloatingExample />
      </Example>
      <Example
        title="Composer focus"
        description="A chip fills the field and focusKey puts the caret back in it, so the member types on from the chip."
        code={composerFocusSource}
        align="start"
      >
        <ComposerFocusExample />
      </Example>
      <Example
        title="Icon button states"
        description="Default, hover, active, and the pressed toggle that reports a flag."
        code={iconButtonsSource}
        align="start"
      >
        <IconButtonStatesExample />
      </Example>
      <DocSection title="States" description="Every control states what it is doing; the lane decides when.">
        <Example
          title="Every state"
          description="A clipped chip row over a wrapped one, an active and a disabled control, and the composer held and sending."
          code={statesSource}
          align="start"
        >
          <ActionBarStates />
        </Example>
        <Example
          title="Interactive states"
          description="The chips filter, the search field clears, the wrap toggles, and a held composer refuses the send."
          code={statesInteractiveSource}
          align="start"
        >
          <ActionBarStatesInteractive />
        </Example>
      </DocSection>
      <DocSection title="API">
        <h3>ActionBar</h3>
        <PropsTable
          rows={[
            { name: "variant", type: '"header" | "toolbar"', default: '"header"', description: "Header is a 32px title row; toolbar holds chips and a search field." },
            { name: "children", type: "ReactNode", description: "Title and actions." },
          ]}
        />
        <h3>ActionBarTitle</h3>
        <PropsTable
          rows={[
            { name: "icon", type: "ReactNode", description: "Leading 16px icon." },
            { name: "children", type: "ReactNode", description: "Lane name. Truncates before the actions." },
            {
              name: "menu",
              type: "boolean | ReactNode",
              default: "false",
              description:
                "true adds the chevron alone. A MenuContent makes the title a menu trigger carrying the chevron, still taking the free width.",
            },
          ]}
        />
        <h3>ActionBarCenter</h3>
        <PropsTable
          rows={[
            {
              name: "children",
              type: "ReactNode",
              description: "One 13px line centred between the title and the actions, cut with an ellipsis.",
            },
          ]}
        />
        <h3>ActionBarActions</h3>
        <PropsTable rows={[{ name: "children", type: "ReactNode", description: "Icon buttons and fields, spaced 10px in a header and 8px in a toolbar." }]} />
        <h3>IconButton</h3>
        <PropsTable
          rows={[
            { name: "label", type: "string", description: "Accessible name. Required." },
            { name: "active", type: "boolean", default: "false", description: "Holds the hover surface open." },
            {
              name: "pressed",
              type: "boolean",
              default: "undefined",
              description:
                "Marks a toggle: reports aria-pressed, lifts the mark to the first ink and fills it. No surface, so it reads apart from active.",
            },
            { name: "disabled", type: "boolean", default: "false", description: "Refuses the press, dims the mark to .5 and drops the hover surface." },
            { name: "children", type: "ReactNode", description: "A 16px icon at stroke 1.5." },
            { name: "...props", type: 'ComponentPropsWithoutRef<"button">', description: "Passed to the button, including type and onClick." },
          ]}
        />
        <h3>Prompts</h3>
        <PropsTable
          rows={[
            { name: "wrap", type: "boolean", default: "false", description: "Runs the chips onto as many lines as they need, and drops the trailing fade." },
            { name: "children", type: "ReactNode", description: "Prompt chips. The row takes the whole width of its parent." },
          ]}
        />
        <h3>Prompt</h3>
        <PropsTable
          rows={[
            { name: "icon", type: "ReactNode", description: "Leading 16px icon." },
            {
              name: "active",
              type: "boolean",
              default: "false",
              description: "Fills the chip and lifts its text to the first ink, for the filter a row is standing on.",
            },
            { name: "disabled", type: "boolean", default: "false", description: "Refuses the press and dims the chip to .5." },
            { name: "children", type: "ReactNode", description: "Chip text." },
            { name: "onClick", type: "() => void", description: "Called when the chip is pressed." },
          ]}
        />
        <h3>SearchField</h3>
        <PropsTable
          rows={[
            { name: "placeholder", type: "string", default: '"Search"', description: "Placeholder text, also the accessible name." },
            { name: "value", type: "string", description: "Controls the input." },
            { name: "onChange", type: "(value: string) => void", description: "Called with the new value." },
            { name: "onClear", type: "() => void", description: "Draws the cross while the value is not empty, and fires when it is pressed." },
          ]}
        />
        <h3>Composer</h3>
        <PropsTable
          rows={[
            { name: "placeholder", type: "string", description: "Placeholder text, also the accessible name. Required." },
            { name: "value", type: "string", description: "Controls the input." },
            { name: "onChange", type: "(value: string) => void", description: "Called with the new value." },
            { name: "onSubmit", type: "(value: string) => void", description: "Called with the current value on Enter or submit. Leaves the input as it is." },
            { name: "leading", type: "ReactNode", description: "Icon slot before the input." },
            { name: "trailing", type: "ReactNode", description: "Icon slot after the input." },
            { name: "disabled", type: "boolean", default: "false", description: "Refuses the field and the send, and dims the whole composer to .5." },
            { name: "busy", type: "boolean", default: "false", description: "Puts Sending in the trailing slot in place of its icons, and refuses the send." },
            { name: "rows", type: "1 | 2", default: "1", description: "Two rows put the field on its own line over the icons, and the field grows to three lines." },
            { name: "surface", type: '"muted" | "outline"', default: '"muted"', description: "Outline stands the composer on the page ground behind a hairline, for a muted container." },
            { name: "floating", type: "boolean", default: "false", description: "Centers the composer at 400px, inverts it to the ink ground and raises it with a shadow." },
            { name: "autoFocus", type: "boolean", default: "false", description: "Takes the caret when the composer first draws." },
            {
              name: "focusKey",
              type: "unknown",
              description:
                "Puts the caret back in the field whenever this value changes, for a chip that fills the draft.",
            },
          ]}
        />
      </DocSection>
    </DocPage>
  );
}
