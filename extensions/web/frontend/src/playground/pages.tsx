import type { ReactNode } from "react";

import type { PropRow, Spread } from "@/playground/docs";
import { StartWordmark } from "@/playground/examples/start-wordmark";
import { StartRows } from "@/playground/examples/start-rows";
import { StartUnlock } from "@/playground/examples/start-unlock";
import { StartWaiting } from "@/playground/examples/start-waiting";
import { TurnColleague } from "@/playground/examples/turn-colleague";
import { TurnColleagueInitial } from "@/playground/examples/turn-colleague-initial";
import { TurnMine } from "@/playground/examples/turn-mine";
import { TurnAgent } from "@/playground/examples/turn-agent";
import { TurnStamp } from "@/playground/examples/turn-stamp";
import { TurnEntering } from "@/playground/examples/turn-entering";
import { MessageCopy } from "@/playground/examples/message-copy";
import { ActivityWorking } from "@/playground/examples/activity-working";
import { ActivitySourcesMany } from "@/playground/examples/activity-sources-many";
import { ActivitySourcesUnknown } from "@/playground/examples/activity-sources-unknown";
import { ActivitySourcesWeb } from "@/playground/examples/activity-sources-web";
import { ActivitySourcesWorkspace } from "@/playground/examples/activity-sources-workspace";
import { ActivitySubagents } from "@/playground/examples/activity-subagents";
import { ActivityThinking } from "@/playground/examples/activity-thinking";
import { ActivityThinkingLoop } from "@/playground/examples/activity-thinking-loop";
import { ActivityThinkingPlain } from "@/playground/examples/activity-thinking-plain";
import { FilesPicture } from "@/playground/examples/files-picture";
import { FilesPictures } from "@/playground/examples/files-pictures";
import { FilesPictureLost } from "@/playground/examples/files-picture-lost";
import { FilesDocument } from "@/playground/examples/files-document";
import { FilesDocumentPlain } from "@/playground/examples/files-document-plain";
import { FilesReport } from "@/playground/examples/files-report";
import { FilesReportName } from "@/playground/examples/files-report-name";
import { FilesAttached } from "@/playground/examples/files-attached";
import { FilesApps } from "@/playground/examples/files-apps";
import { AskedChoices } from "@/playground/examples/asked-choices";
import { AskedMulti } from "@/playground/examples/asked-multi";
import { AskedProse } from "@/playground/examples/asked-prose";
import { AskedStepper } from "@/playground/examples/asked-stepper";
import { AskedSettled } from "@/playground/examples/asked-settled";
import { OfferOne } from "@/playground/examples/offer-one";
import { OffersSeveral } from "@/playground/examples/offers-several";
import { OfferLong } from "@/playground/examples/offer-long";
import { HandoffConnect } from "@/playground/examples/handoff-connect";
import { HandoffConnected } from "@/playground/examples/handoff-connected";
import { HandoffCredential } from "@/playground/examples/handoff-credential";
import { TranscriptDefault } from "@/playground/examples/transcript-default";
import { TranscriptMarked } from "@/playground/examples/transcript-marked";
import { TranscriptLettingGo } from "@/playground/examples/transcript-letting-go";
import { TranscriptLoading } from "@/playground/examples/transcript-loading";
import { TranscriptEmpty } from "@/playground/examples/transcript-empty";
import { TranscriptEarlierLoading } from "@/playground/examples/transcript-earlier-loading";
import { TranscriptEarlierFailed } from "@/playground/examples/transcript-earlier-failed";
import { TranscriptEarlierSettled } from "@/playground/examples/transcript-earlier-settled";
import { TranscriptWatching } from "@/playground/examples/transcript-watching";
import { ComposerResting } from "@/playground/examples/composer-resting";
import { ComposerFiles } from "@/playground/examples/composer-files";
import { ComposerStops } from "@/playground/examples/composer-stops";
import { ComposerEyebrow } from "@/playground/examples/composer-eyebrow";
import { ComposerAttention } from "@/playground/examples/composer-attention";
import { ToastSurface } from "@/playground/examples/toast-surface";
import { ToastDescription } from "@/playground/examples/toast-description";
import { ToastSilent } from "@/playground/examples/toast-silent";
import { ButtonSend } from "@/playground/examples/button-send";
import { ButtonOutline } from "@/playground/examples/button-outline";
import { ButtonRow } from "@/playground/examples/button-row";
import { ButtonQuiet } from "@/playground/examples/button-quiet";
import { ButtonQuietPressed } from "@/playground/examples/button-quiet-pressed";
import { ButtonOptionPressed } from "@/playground/examples/button-option-pressed";
import { ButtonMark } from "@/playground/examples/button-mark";
import { ButtonCorner } from "@/playground/examples/button-corner";
import { ButtonSizeDefault } from "@/playground/examples/button-size-default";
import { ButtonSizeCommit } from "@/playground/examples/button-size-commit";
import { ButtonSizeBar } from "@/playground/examples/button-size-bar";
import { ButtonSizeChip } from "@/playground/examples/button-size-chip";
import { ButtonSizeIcon } from "@/playground/examples/button-size-icon";
import { ButtonSizeGlyph } from "@/playground/examples/button-size-glyph";
import { ButtonToneSoft } from "@/playground/examples/button-tone-soft";
import { ButtonToneAttention } from "@/playground/examples/button-tone-attention";
import { ButtonBusy } from "@/playground/examples/button-busy";
import { ButtonDisabled } from "@/playground/examples/button-disabled";

export type Example = {
  /** Which run of states this one belongs beside, so an axis reads across in one line. */
  group: string;
  /** The name of the state, carrying any qualifier the tile cannot show, such as on hover. */
  title: string;
  /** A one-shot animation, which has finished before a reader arrives: the state needs replaying. */
  replay?: boolean;
  render: () => ReactNode;
};
export type Page = {
  slug: string;
  name: string;
  modules: string[];
  spread: Spread;
  owns: string;
  /** The `cva` axes whose values each draw differently, held one state per value by the tests. */
  axes?: string[];
  examples: Example[];
  props: { of: string; rows: PropRow[] }[];
};

export const CHAT_PAGES: Page[] = [
  {
    slug: "start",
    name: "Start",
    modules: ["starters.tsx", "pressrow.tsx"],
    spread: "full",
    owns: "The screen a conversation opens on: the mark over the composer, the rows the workspace ranked under it, the accounts a row still needs, and what stands in their place while the ranking is read.",
    examples: [
      {
        group: "The first screen",
        title: "the mark over the composer",
        render: () => <StartWordmark />,
      },
      {
        group: "The first screen",
        title: "the rows a member can press",
        render: () => <StartRows />,
      },
      {
        group: "The first screen",
        title: "a row naming the accounts it needs",
        render: () => <StartUnlock />,
      },
      {
        group: "Before the ranking lands",
        title: "the rows still being ranked",
        render: () => <StartWaiting />,
      },
    ],
    props: [
      {
        of: "Starters",
        rows: [
          { name: "rows", type: "StarterRow[]", description: "The ranked rows, in order. FALLBACK_ROWS is what stands there when the read names none." },
          { name: "unlock", type: "UnlockRow | null", description: "The row whose work needs an account the workspace has not connected. It names the providers in its note. Null draws the link to the connectors screen in its place." },
          { name: "waiting", type: "boolean", description: "The ranking is still being read, so every row draws as its own place rather than as a row a press could take away." },
          { name: "onStart", type: "(agentId: string | null | undefined, ask: string, kind: string) => void", description: "What a press sends. The row carries the whole ask, not the few words it reads as, and names the application it belongs to." },
        ],
      },
      {
        of: "Wordmark",
        rows: [
          { name: "(none)", type: "—", description: "The logo over the composer, masked out of the current ink. It is drawn at the hero size and taken away below the narrow breakpoint, where the composer needs the height." },
        ],
      },
      {
        of: "StarterWaiting",
        rows: [
          { name: "width", type: "string", description: "How much of the row the line will fill. Three rows take three widths, so the wait reads as rows of prose rather than as a bar chart." },
        ],
      },
      {
        of: "ConnectWaiting",
        rows: [
          { name: "(none)", type: "—", description: "The connector row's place while the ranking is read. The ranking decides whether that row names one application's accounts or the connectors screen, so no act is drawn before it lands." },
        ],
      },
      {
        of: "PressRow",
        rows: [
          { name: "line", type: "string", description: "What the row says. One sentence, cut to the measure the row leaves it and travelling under the pointer; at phone widths it wraps and the row grows instead." },
          { name: "note", type: "string", description: "The trailing detail, in the same type as the line, so the row reads as one sentence. It is what names the accounts an unlock row still needs." },
          { name: "glyph", type: "ReactNode", description: "The mark before the line. Every row that sends a sentence draws AskMark, so the mark says what the press does rather than what kind of thing the row is." },
          { name: "onPress", type: "() => void", description: "The verb. The row is a button." },
        ],
      },
    ],
  },
  {
    slug: "message",
    name: "Message",
    modules: ["message.tsx", "bubble.tsx", "bubble-header.tsx", "marker.tsx", "meta.tsx", "avatar.tsx", "copy-act.tsx", "lib/memberFace.tsx"],
    spread: "wide",
    owns: "One turn in a transcript: the side it runs from, the face and the name beside it, the fill the words are read on, and the stamp under them. A page states who is speaking and nothing else.",
    examples: [
      {
        group: "Who is speaking",
        title: "a colleague's turn",
        render: () => <TurnColleague />,
      },
      {
        group: "Who is speaking",
        title: "a colleague with no picture",
        render: () => <TurnColleagueInitial />,
      },
      {
        group: "Who is speaking",
        title: "your own turn",
        render: () => <TurnMine />,
      },
      {
        group: "Who is speaking",
        title: "the agent's reply",
        render: () => <TurnAgent />,
      },
      {
        group: "States",
        title: "the stamp under a turn (on hover)",
        render: () => <TurnStamp />,
      },
      {
        group: "States",
        title: "the act that copies a reply",
        render: () => <MessageCopy />,
      },
      {
        group: "States",
        title: "a turn arriving",
        replay: true,
        render: () => <TurnEntering />,
      },
    ],
    props: [
      {
        of: "Message",
        rows: [
          { name: "align", type: "\"start\" | \"end\"", fallback: "\"start\"", description: "Which side the turn runs from. It reverses the row and carries every part with it." },
          { name: "...props", type: "ComponentProps<\"div\">", description: "Everything a div takes." },
        ],
      },
      {
        of: "MessageContent",
        rows: [
          { name: "...props", type: "ComponentProps<\"div\">", description: "Everything a div takes. It stacks the turn's parts, and stands each of them right under align=\"end\"." },
        ],
      },
      {
        of: "MessageHeader",
        rows: [
          { name: "...props", type: "ComponentProps<\"div\">", description: "Everything a div takes. The small soft line over the bubble, inset by the bubble's padding and un-inset beside a ghost one." },
        ],
      },
      {
        of: "Bubble",
        rows: [
          { name: "variant", type: "\"default\" | \"said\" | \"ghost\"", fallback: "\"default\"", description: "Which fill the words are read on. said is the one that keeps typed line breaks; ghost drops the fill, the rounding and the inset, so the words run the full column." },
          { name: "align", type: "\"start\" | \"end\"", fallback: "\"start\"", description: "Which side of the row the bubble stands on." },
          { name: "entering", type: "boolean", fallback: "false", description: "Fades the turn in from transparent over 300ms. Set it for the turn that just landed, never for the transcript behind it." },
          { name: "...props", type: "ComponentProps<\"div\">", description: "Everything a div takes." },
        ],
      },
      {
        of: "BubbleContent",
        rows: [
          { name: "...props", type: "ComponentProps<\"div\">", description: "Everything a div takes. Its measure, inset, radius and reading leading are the component's." },
        ],
      },
      {
        of: "BubbleHeader",
        rows: [
          { name: "speaker", type: "Speaker", description: "Who said it. Only the name is drawn, and it truncates rather than widening the row." },
          { name: "className", type: "string", description: "Layout only; the inset and the type step are the component's." },
        ],
      },
      {
        of: "Marker",
        rows: [
          { name: "variant", type: "\"default\" | \"stamp\"", fallback: "\"default\"", description: "The type step. A stamp is a size down, on tabular figures, so a count does not change width as it rises." },
          { name: "align", type: "\"start\" | \"end\"", fallback: "\"start\"", description: "Which end of the row the line reads from." },
          { name: "indent", type: "boolean", fallback: "false", description: "Sets the line in 16px, for a step that belongs to the step above it." },
          { name: "reveal", type: "boolean", fallback: "false", description: "Holds the line at opacity 0 until a pointer or keyboard focus reaches a group/message ancestor. Without that ancestor it never shows." },
          { name: "render", type: "ReactElement", description: "An element to draw as, where the marker must be a link or a button. The marker's classes and props are cloned onto it." },
          { name: "...props", type: "ComponentProps<\"div\">", description: "Everything a div takes." },
        ],
      },
      {
        of: "Meta",
        rows: [
          { name: "model", type: "string | null", description: "The model that answered, drawn as its mark alone. Its name reaches the member on hover and on focus." },
          { name: "last", type: "boolean", fallback: "true", description: "The last turn carries its line openly. An earlier one reveals it on hover and on keyboard focus, keeping its space either way so revealing it moves nothing above it." },
          { name: "mine", type: "boolean", fallback: "false", description: "Ends the line where the member's own words end." },
          { name: "copy", type: "string", description: "The words the copy act puts on the clipboard. No copy act is drawn without it." },
          { name: "children", type: "ReactNode", description: "The spend, the stamp, or the fault the turn ended on." },
        ],
      },
      {
        of: "MarkerContent",
        rows: [
          { name: "working", type: "boolean", fallback: "false", description: "Passes a highlight band through the words every 2 seconds while the step they name runs. A member who asked for less motion gets the words unmoved." },
          { name: "truncate", type: "boolean", fallback: "false", description: "Cuts the line to the width it is given, ending in an ellipsis." },
          { name: "...props", type: "ComponentProps<\"span\">", description: "Everything a span takes." },
        ],
      },
      {
        of: "FaceCircle",
        rows: [
          { name: "name", type: "string", description: "The name the initial is taken from. One letter: a second is a name the portal does not hold." },
          { name: "photo", type: "string | null", description: "The stored picture, where the member has one. photoAddress() spells the portal address of the path the wire carries." },
          { name: "tint", type: "string", description: "What the colour behind the initial is keyed off. Pass the member's address wherever the caller holds one: a name changes and an address does not." },
          { name: "title", type: "string", description: "What a pointer resting on the circle says." },
          { name: "className", type: "string", description: "Layout only, such as the size a stack draws its faces at." },
          { name: "style", type: "CSSProperties", description: "Layout only, such as the offset a face takes in a stack." },
        ],
      },
      {
        of: "Avatar",
        rows: [
          { name: "...props", type: "ComponentProps<Root>", description: "Everything the Radix root takes. The bare circle carries no person's colour, so it is what an app's mark and a company's are drawn in; a person is drawn as FaceCircle." },
        ],
      },
      {
        of: "AvatarImage",
        rows: [
          { name: "src", type: "string", description: "The picture. Radix holds the fallback until it decodes and drops back to it if it never does. FaceCircle draws this one where a member has a stored photo." },
          { name: "...props", type: "ComponentProps<Image>", description: "Everything the Radix image takes." },
        ],
      },
      {
        of: "AvatarFallback",
        rows: [
          { name: "...props", type: "ComponentProps<Fallback>", description: "Everything the Radix fallback takes. With no AvatarImage beside it, it draws the moment it mounts, which is how an app's mark and a member with no photo are drawn." },
        ],
      },
      {
        of: "CopyAct",
        rows: [
          { name: "text", type: "string", description: "The words the press puts on the clipboard: an agent's reply as it was written, a member's own words as they typed them. The act draws the copy mark at rest, a tick reading Copied for two seconds once the clipboard takes them, and a cross in the attention ink where the browser refused — each of those is the act's own state, not one a page sets." },
        ],
      },
    ],
  },
  {
    slug: "activity",
    name: "Activity",
    modules: ["turn-activity.tsx", "marker.tsx", "decode.tsx", "sources.tsx"],
    spread: "full",
    owns: "Everything a turn draws under itself while it runs: the step it is on, that step resolving out of braille, the pages and records it read, and the steps of the subagents it waits on.",
    examples: [
      {
        group: "Steps under a turn",
        title: "the step a turn is on",
        render: () => <ActivityWorking />,
      },
      {
        group: "Steps under a turn",
        title: "a step arriving",
        replay: true,
        render: () => <ActivityThinking />,
      },
      {
        group: "Steps under a turn",
        title: "loop (a step that outlasts its words)",
        render: () => <ActivityThinkingLoop />,
      },
      {
        group: "Steps under a turn",
        title: "color={false}",
        render: () => <ActivityThinkingPlain />,
      },
      {
        group: "Steps under a turn",
        title: "the steps of the subagents it waits on",
        render: () => <ActivitySubagents />,
      },
      {
        group: "What the turn read",
        title: "pages on the web",
        render: () => <ActivitySourcesWeb />,
      },
      {
        group: "What the turn read",
        title: "records in the workspace",
        render: () => <ActivitySourcesWorkspace />,
      },
      {
        group: "What the turn read",
        title: "more than the row holds",
        render: () => <ActivitySourcesMany />,
      },
      {
        group: "What the turn read",
        title: "a source that draws no mark of its own",
        render: () => <ActivitySourcesUnknown />,
      },
    ],
    props: [
      {
        of: "TurnActivity",
        rows: [
          { name: "working", type: "string | null", description: "The step the turn is on, as the turn last named it. Null draws no step line, and a turn holding no running run then draws nothing at all: its reply is what it did." },
          { name: "runs", type: "SubagentRun[]", description: "Every subagent the turn started. The running ones are counted in place of the step — Awaiting 2 subagents — and each draws its own step indented under it, because a count alone reads as a stuck turn." },
          { name: "sources", type: "SourceRef[]", fallback: "[]", description: "What the turn read, drawn between the step and the subagents." },
        ],
      },
      {
        of: "DecodeLine",
        rows: [
          { name: "text", type: "string", description: "The words the cells resolve into. A new text glyphs in from the churn rather than inheriting how far the one before it had got." },
          { name: "loop", type: "boolean", fallback: "false", description: "Churns again after it settles, for a step that outlasts the words naming it." },
          { name: "color", type: "boolean", fallback: "true", description: "Carries the ripple and pulse through the cells. Off leaves the churn in the line's own ink." },
          { name: "delay", type: "number", fallback: "0", description: "Milliseconds before the churn starts, so a run of lines does not resolve in unison." },
          { name: "className", type: "string", description: "Layout only." },
        ],
      },
      {
        of: "Sources",
        rows: [
          { name: "sources", type: "SourceRef[]", description: "What the turn read. Up to 8 tiles are drawn, deduplicated, and the count states the whole. An empty list draws nothing." },
        ],
      },
      {
        of: "SourceTile",
        rows: [
          { name: "source", type: "SourceRef", description: "One page or record. A web source draws its favicon, falling back to a globe when it never decodes; a workspace source draws its provider's mark, falling back to a page glyph when it names none." },
        ],
      },
      {
        of: "Marker",
        rows: [
          { name: "variant", type: "\"default\" | \"stamp\"", fallback: "\"default\"", description: "The type step. A stamp is a size down, on tabular figures." },
          { name: "align", type: "\"start\" | \"end\"", fallback: "\"start\"", description: "Which end of the row the line reads from." },
          { name: "indent", type: "boolean", fallback: "false", description: "Sets the line in 16px, for a subagent's step under the step that started it." },
          { name: "reveal", type: "boolean", fallback: "false", description: "Holds the line at opacity 0 until a pointer or keyboard focus reaches a group/message ancestor." },
          { name: "render", type: "ReactElement", description: "An element to draw as, where a step opens something and must be a link or a button." },
          { name: "...props", type: "ComponentProps<\"div\">", description: "Everything a div takes." },
        ],
      },
      {
        of: "MarkerContent",
        rows: [
          { name: "working", type: "boolean", fallback: "false", description: "Passes a highlight band through the words every 2 seconds while the step runs, and loops until the caller drops the flag. A member who asked for less motion gets the words unmoved." },
          { name: "truncate", type: "boolean", fallback: "false", description: "Cuts the step to the width it is given, ending in an ellipsis." },
          { name: "...props", type: "ComponentProps<\"span\">", description: "Everything a span takes." },
        ],
      },
    ],
  },
  {
    slug: "files",
    name: "Files",
    modules: ["turn-files.tsx", "turn-apps.tsx", "kernel/messages.tsx"],
    spread: "full",
    owns: "What a turn sends back and what a member sends with their own words: the pictures, the documents, the report a long answer is carried in, and the applications a turn built.",
    examples: [
      {
        group: "Pictures a turn sent",
        title: "one picture",
        render: () => <FilesPicture />,
      },
      {
        group: "Pictures a turn sent",
        title: "several pictures",
        render: () => <FilesPictures />,
      },
      {
        group: "Pictures a turn sent",
        title: "a picture whose preview never decodes",
        render: () => <FilesPictureLost />,
      },
      {
        group: "Documents a turn sent",
        title: "a document with a cover",
        render: () => <FilesDocument />,
      },
      {
        group: "Documents a turn sent",
        title: "a document with no cover",
        render: () => <FilesDocumentPlain />,
      },
      {
        group: "The report a turn carried",
        title: "the report, with an address",
        render: () => <FilesReport />,
      },
      {
        group: "The report a turn carried",
        title: "the report, with no address",
        render: () => <FilesReportName />,
      },
      {
        group: "What a member attached",
        title: "the files on a member's own turn",
        render: () => <FilesAttached />,
      },
      {
        group: "What a turn built",
        title: "the applications a turn built",
        render: () => <FilesApps />,
      },
    ],
    props: [
      {
        of: "TurnFiles",
        rows: [
          { name: "files", type: "ChatFile[]", description: "Everything the turn sent. It sorts them itself: the details role first as the carried report, then the pictures that have a preview, then everything else as document cards two to a row." },
          { name: "onOpen", type: "(opened: Opened) => void", description: "What a press hands back: the files to move through and which of them was pressed. Pictures open on the whole run, a document on itself alone." },
        ],
      },
      {
        of: "FilePicture",
        rows: [
          { name: "file", type: "ChatFile", description: "The picture. It is drawn at its own size up to --media-card, with the kind's badge over its lower left, and falls back to the thumbnail square when the preview never decodes." },
          { name: "onOpen", type: "() => void", description: "What the press opens." },
          { name: "grouped", type: "boolean", fallback: "false", description: "The picture stands in a run of pictures, so it keeps its width and snaps to the start of the scroller instead of taking the top margin of a lone one." },
        ],
      },
      {
        of: "FileCard",
        rows: [
          { name: "file", type: "ChatFile", description: "The document. The cover is drawn where the file has a preview, and the size under the name where the wire carried one." },
          { name: "onOpen", type: "() => void", description: "What the press opens. Both the cover and the name take it, so the whole card is the act." },
        ],
      },
      {
        of: "CarriedReport",
        rows: [
          { name: "file", type: "ChatFile", description: "The answer too long for a bubble. Its subject is what the link reads, or Open detailed report where it carries none; a file with no address is its filename in the mono step, since there is nothing to open." },
          { name: "onOpen", type: "() => void", description: "What the press opens." },
        ],
      },
      {
        of: "AttachedFiles",
        rows: [
          { name: "files", type: "ChatFile[]", description: "What the member sent, once the turn has taken them. Each is a thumbnail that opens the run." },
          { name: "picked", type: "File[]", description: "What the member sent that is still going up, drawn in the browser off the picked file itself. It stands in place of files while it holds anything, so one send never draws twice." },
          { name: "onOpen", type: "(opened: Opened) => void", description: "What a press hands back." },
        ],
      },
      {
        of: "OpenedFile",
        rows: [
          { name: "opened", type: "Opened", description: "The files a press opened and which of them stands open: { files, at }. A picture opens in the lightbox and everything else in the file sheet." },
          { name: "onMove", type: "(at: number) => void", description: "Called as the member moves through the run." },
          { name: "onClose", type: "() => void", description: "Called when they close it." },
        ],
      },
      {
        of: "TurnApps",
        rows: [
          { name: "apps", type: "ChatApp[]", description: "The applications the turn built, each a row that opens its screen: the mark it was given, its name as a member reads it, and the model it runs on." },
        ],
      },
    ],
  },
  {
    slug: "question",
    name: "Question",
    modules: ["asked.tsx", "questionnaire.tsx"],
    spread: "full",
    owns: "The card a turn asks a question in and waits on: the answers already given, the choices, the write-in beside them, the keys that reach each one, the refusal, and the way through more than one question.",
    examples: [
      {
        group: "A question a turn asks",
        title: "a question with choices and a write-in",
        render: () => <AskedChoices />,
      },
      {
        group: "A question a turn asks",
        title: "a question that takes more than one answer",
        render: () => <AskedMulti />,
      },
      {
        group: "A question a turn asks",
        title: "a question answered in the composer",
        render: () => <AskedProse />,
      },
      {
        group: "More than one question",
        title: "several questions, one at a time",
        render: () => <AskedStepper />,
      },
      {
        group: "More than one question",
        title: "the answers already given",
        render: () => <AskedSettled />,
      },
    ],
    props: [
      {
        of: "Asked",
        rows: [
          { name: "question", type: "ChatQuestion", description: "The whole ask: its title, the application's mark beside it, every question in order, and the answers that have landed. A question offering between two and ten choices is drawn as choices, one that takes a file or names more is answered in the composer, and the rest take the write-in alone." },
          { name: "held", type: "boolean", description: "The turn is still running, so the submit is refused until it settles." },
          { name: "onAnswer", type: "(answers: FormData, open: { entry, index }[]) => void", description: "The answers, with the questions they belong to. Only the questions the card drew are handed back, so a question answered in the composer is not counted as skipped." },
        ],
      },
      {
        of: "Settled",
        rows: [
          { name: "rows", type: "SettledRow[]", description: "The questions already answered, each with the words the member gave. The key beside a row is the letter the choice carried, or the write-in's letter where the words match no choice." },
          { name: "restated", type: "boolean", description: "An ask of more than one question sends each answer with its question restated after it, so the row cuts that restatement back off before drawing the words." },
        ],
      },
      {
        of: "Questionnaire",
        rows: [
          { name: "items", type: "QuestionnaireItemDefinition[]", description: "Every question, in order, with the choices each one offers." },
          { name: "item", type: "string", description: "Which question is open." },
          { name: "onItemChange", type: "(name: string) => void", description: "Called when the member moves between questions." },
          { name: "onSubmit", type: "(event: FormEvent) => void", description: "The answers, as form data." },
          { name: "...props", type: "ComponentProps<Root>", description: "Everything the primitive's root takes, except shortcuts: the letters are the component's." },
        ],
      },
      {
        of: "QuestionnaireItem",
        rows: [
          { name: "name", type: "string", description: "Which question this is, matched against the root's item." },
          { name: "required", type: "boolean", description: "An answer is needed before the member can go on." },
          { name: "invalid", type: "boolean", description: "Draws the choices in the attention edge and opens the refusal under them." },
          { name: "...props", type: "ComponentProps<Item>", description: "Everything the primitive's item takes." },
        ],
      },
      {
        of: "QuestionnaireTitle",
        rows: [
          { name: "...props", type: "ComponentProps<Title>", description: "Everything the primitive's title takes. The question itself, at the subtitle step." },
        ],
      },
      {
        of: "QuestionnaireChoices",
        rows: [
          { name: "...props", type: "ComponentProps<Choices>", description: "Everything the primitive's choices take. It stacks the rows and holds the write-in with them." },
        ],
      },
      {
        of: "QuestionnaireChoice",
        rows: [
          { name: "value", type: "string", description: "What the answer carries." },
          { name: "disabled", type: "boolean", description: "The row cannot be chosen: opacity 0.4 and no pointer events." },
          { name: "...props", type: "ComponentProps<Choice>", description: "Everything the primitive takes. A chosen row takes the strong edge, the fill, and an inverted key." },
        ],
      },
      {
        of: "QuestionnaireChoiceDescription",
        rows: [
          { name: "...props", type: "ComponentProps<\"span\">", description: "Everything a span takes. Two lines under the choice, opened whole once the row is chosen." },
        ],
      },
      {
        of: "QuestionnaireInput",
        rows: [
          { name: "shortcut", type: "string", description: "The letter that reaches the write-in." },
          { name: "...props", type: "ComponentProps<Input>", description: "Everything the primitive takes. Once it holds words, the row takes the accent edge and the key inverts." },
        ],
      },
      {
        of: "QuestionnaireError",
        rows: [
          { name: "...props", type: "ComponentProps<Error>", description: "Everything the primitive takes. The refusal, in the attention ink, drawn only while the question is invalid." },
        ],
      },
      {
        of: "QuestionnaireActions",
        rows: [
          { name: "...props", type: "ComponentProps<\"div\">", description: "Everything a div takes. The row under the question: the stepper stands left, the acts right." },
        ],
      },
      {
        of: "QuestionnaireStepper",
        rows: [
          { name: "...props", type: "ComponentProps<\"div\">", description: "Everything a div takes. Back and next around the count of the question the member is on." },
        ],
      },
      {
        of: "QuestionnaireSkip",
        rows: [
          { name: "children", type: "ReactNode", description: "What the act says. Unset, Skip." },
          { name: "...props", type: "ComponentProps<Skip>", description: "Everything the primitive takes. It is drawn as an outline bar button." },
        ],
      },
      {
        of: "QuestionnaireOnward",
        rows: [
          { name: "children", type: "ReactNode", description: "What the act says. Unset, Continue with the return mark." },
          { name: "...props", type: "ComponentProps<Next>", description: "Everything the primitive's next takes. It is drawn as a send bar button." },
        ],
      },
      {
        of: "QuestionnaireSubmit",
        rows: [
          { name: "children", type: "ReactNode", description: "What the act says. Unset, Continue with the return mark." },
          { name: "...props", type: "ComponentProps<Submit>", description: "Everything the primitive's submit takes. It stands in place of Onward on the last question." },
        ],
      },
    ],
  },
  {
    slug: "offers",
    name: "Offers",
    modules: ["offers.tsx", "ticker.tsx"],
    spread: "full",
    owns: "The rows a settled thread ends on, which the product calls follow-ups: what the agent offers to do next, each row a few words that send a whole sentence as the member's own.",
    examples: [
      {
        group: "The rows a settled thread ends on",
        title: "one offer",
        render: () => <OfferOne />,
      },
      {
        group: "The rows a settled thread ends on",
        title: "several offers",
        render: () => <OffersSeveral />,
      },
      {
        group: "The rows a settled thread ends on",
        title: "an offer past the measure (travelling on hover)",
        render: () => <OfferLong />,
      },
    ],
    props: [
      {
        of: "OfferRows",
        rows: [
          { name: "offers", type: "{ hook: string; prompt: string }[]", description: "What the agent offers next. The hook is the few words the row reads as; the prompt is the sentence the press sends, which states the work in full. An empty list draws nothing, rule and all." },
          { name: "onPress", type: "(prompt: string) => void", description: "What a press sends. It carries the prompt, never the hook, so the thread reads as the member having asked for the work." },
        ],
      },
      {
        of: "AskMark",
        rows: [
          { name: "(none)", type: "—", description: "The arrow every row that sends a sentence draws, here and on the start screen. It says the press sends the words, which is the one thing these rows have to say before they are read." },
        ],
      },
      {
        of: "Ticker",
        rows: [
          { name: "asks", type: "number", description: "How many times the pointer or the focus has arrived. A row raises it on enter and on focus and drops it to 0 on leave. Raising it rests 300ms, then travels the words out to the last one at 45 pixels a second; a member who asked for less motion keeps the cut and never the travel." },
          { name: "className", type: "string", description: "Layout only." },
          { name: "children", type: "ReactNode", description: "The words. A line past its column fades out over its last 32px rather than ending in three dots, and a line out travelling fades at the leading edge instead." },
        ],
      },
    ],
  },
  {
    slug: "handoff",
    name: "Handoff",
    modules: ["connect-link.tsx", "handoff.tsx"],
    spread: "full",
    owns: "What a turn needs from the member before it can go on: the account it asks them to connect, and the credential it asks them to paste.",
    examples: [
      {
        group: "An account a turn asks for",
        title: "before connecting",
        render: () => <HandoffConnect />,
      },
      {
        group: "An account a turn asks for",
        title: "once the account is connected",
        render: () => <HandoffConnected />,
      },
      {
        group: "A credential a turn asks for",
        title: "the credential card",
        render: () => <HandoffCredential />,
      },
    ],
    props: [
      {
        of: "ConnectLink",
        rows: [
          { name: "connect", type: "ChatConnect", description: "What the turn asked for. turn is the turn the consent is minted against, and the chip is a link while it stands; without it the account is connected and the chip states so, with the address it was connected as where the wire carried one. provider draws the brand mark, and label names the account in both readings." },
        ],
      },
      {
        of: "Handoff",
        rows: [
          { name: "children", type: "ReactNode", description: "The reason the turn gives and a form for each credential it needs. The card is the bubble's radius and edge with no fill, so it reads as the agent's turn without standing as speech." },
        ],
      },
    ],
  },
  {
    slug: "transcript",
    name: "Transcript",
    modules: ["message-scroller.tsx", "earlier-row.tsx", "watching.tsx", "empty.tsx", "skeleton.tsx", "kernel/panel.tsx"],
    spread: "full",
    owns: "The scroll a conversation is read in: where it opens, whether a move animates, the row a jump lands on, the act that returns to the foot, what stands at its head while older turns load, and what stands there before any turn has arrived.",
    examples: [
      {
        group: "A transcript of turns",
        title: "several turns",
        render: () => <TranscriptDefault />,
      },
      {
        group: "The row a jump landed on",
        title: "the row it landed on",
        replay: true,
        render: () => <TranscriptMarked />,
      },
      {
        group: "The row a jump landed on",
        title: "the mark fading",
        render: () => <TranscriptLettingGo />,
      },
      {
        group: "Before the turns arrive",
        title: "a transcript still arriving",
        render: () => <TranscriptLoading />,
      },
      {
        group: "Before the turns arrive",
        title: "a transcript with nothing in it",
        render: () => <TranscriptEmpty />,
      },
      {
        group: "At the head of the transcript",
        title: "earlier messages loading",
        render: () => <TranscriptEarlierLoading />,
      },
      {
        group: "At the head of the transcript",
        title: "earlier messages that would not load",
        render: () => <TranscriptEarlierFailed />,
      },
      {
        group: "At the head of the transcript",
        title: "nothing earlier to load",
        render: () => <TranscriptEarlierSettled />,
      },
      {
        group: "A conversation you are only watching",
        title: "the one act a watcher is offered",
        render: () => <TranscriptWatching />,
      },
    ],
    props: [
      {
        of: "MessageScrollerProvider",
        rows: [
          { name: "autoScroll", type: "boolean", fallback: "false", description: "Follows the foot as rows land, while the member is standing at the foot." },
          { name: "defaultScrollPosition", type: "\"start\" | \"end\" | \"last-anchor\"", fallback: "\"end\"", description: "Where a transcript opens. last-anchor opens on the row the member last read." },
          { name: "scrollEdgeThreshold", type: "number", fallback: "8", description: "How many pixels off the foot still count as standing at the foot." },
          { name: "scrollPreviousItemPeek", type: "number", fallback: "64", description: "How much of the row above a jump leaves showing, so the landing reads as a place in a transcript." },
          { name: "scrollMargin", type: "number", description: "The gap a jump leaves over the row it lands on." },
        ],
      },
      {
        of: "MessageScroller",
        rows: [
          { name: "...props", type: "ComponentProps<Root>", description: "Everything the primitive's root takes. It holds the viewport and fades the foot while there is more below." },
        ],
      },
      {
        of: "MessageScrollerViewport",
        rows: [
          { name: "animate", type: "boolean", fallback: "false", description: "Animates the scrolls the component makes. Off by default: opening a transcript, switching to another and restoring the place after older rows load all have to land without moving." },
          { name: "preserveScrollOnPrepend", type: "boolean", fallback: "true", description: "Holds the reading place when older rows load in above it." },
          { name: "...props", type: "ComponentProps<Viewport>", description: "Everything the primitive's viewport takes." },
        ],
      },
      {
        of: "MessageScrollerContent",
        rows: [
          { name: "...props", type: "ComponentProps<Content>", description: "Everything the primitive's content takes. It stacks the rows 16px apart and fills the viewport." },
        ],
      },
      {
        of: "MessageScrollerItem",
        rows: [
          { name: "messageId", type: "string", description: "The id a jump names. A row without one cannot be jumped to." },
          { name: "mark", type: "\"held\" | \"letting-go\"", fallback: "—", description: "The mark a jump leaves on the row it landed on: held is the second accent as a fill and a 2px outline, letting-go crosses both to transparent over 500ms." },
          { name: "throb", type: "boolean", fallback: "false", description: "Pulses the mark twice over 1.5s as the row arrives. The caller reads the motion preference, so a member who asked for less motion gets the hold and the fade without it." },
          { name: "scrollAnchor", type: "boolean", fallback: "false", description: "Anchors the viewport to this row, so rows landing elsewhere do not move it." },
          { name: "...props", type: "ComponentProps<Item>", description: "Everything the primitive's item takes." },
        ],
      },
      {
        of: "MessageScrollerButton",
        rows: [
          { name: "behavior", type: "ScrollBehavior", description: "How the jump moves. Unset, it reads the motion preference and jumps outright where less motion is asked for." },
          { name: "children", type: "ReactNode", description: "What the act draws. Unset, the chevron, labelled Jump to bottom." },
          { name: "render", type: "ReactElement", description: "The element it draws as. Unset, an outline icon button. It slides out of the foot and fades while the member is already there." },
        ],
      },
      {
        of: "Skeleton",
        rows: [
          { name: "className", type: "string", description: "The box the answer will fill. Layout only: the ground and the pulse are the component's, and it stays invisible for 250ms before it fades in, so a read that answers at once draws nothing." },
        ],
      },
      {
        of: "Empty",
        rows: [
          { name: "...props", type: "ComponentProps<\"div\">", description: "Everything a div takes. It draws the card and the dashed frame inside it, and centres whatever it holds." },
        ],
      },
      {
        of: "EmptyHeader",
        rows: [
          { name: "...props", type: "ComponentProps<\"div\">", description: "Everything a div takes. It stacks the mark, the title and the description, and holds them to the hint measure." },
        ],
      },
      {
        of: "EmptyMedia",
        rows: [
          { name: "children", type: "ReactNode", description: "One mark, drawn at the glyph size in the soft ink." },
        ],
      },
      {
        of: "EmptyTitle",
        rows: [
          { name: "...props", type: "ComponentProps<\"p\">", description: "Everything a p takes. The body step in the soft ink." },
        ],
      },
      {
        of: "EmptyDescription",
        rows: [
          { name: "...props", type: "ComponentProps<\"p\">", description: "Everything a p takes. The label step in the quiet ink, a step under the title." },
        ],
      },
      {
        of: "EarlierRow",
        rows: [
          { name: "earlier", type: "EarlierMessages", description: "The pages above the transcript and the read that fetches them. The row watches for itself reaching the viewport and loads the next page there, states the wait while it runs, and offers the retry where it failed. With nothing left above, the row draws nothing and keeps the head of the scroll." },
        ],
      },
      {
        of: "scrollerOf",
        rows: [
          { name: "node", type: "Element", description: "The row. It answers the nearest ancestor that scrolls, falling back to the document. The row holds that scroller's place against the turn below it and puts it back once the page has landed, so loading older turns never moves what the member is reading." },
        ],
      },
      {
        of: "Watching",
        rows: [
          { name: "onStop", type: "() => Promise<void>", description: "What the stop does. It is the one act a conversation the member is only watching offers, standing where the composer would; the act draws as busy until the promise settles. The surface decides who may press it." },
        ],
      },
    ],
  },
  {
    slug: "composer",
    name: "Composer",
    modules: ["prompt-input.tsx", "attachment.tsx"],
    spread: "full",
    owns: "The card a member writes in: the files it holds, the line above it, and the act that sends or stops. A page states what it is sending, never how the card is drawn.",
    examples: [
      {
        group: "States",
        title: "resting",
        render: () => <ComposerResting />,
      },
      {
        group: "States",
        title: "with files attached",
        render: () => <ComposerFiles />,
      },
      {
        group: "States",
        title: "a turn is running",
        render: () => <ComposerStops />,
      },
      {
        group: "The line above the composer",
        title: "the line above it",
        render: () => <ComposerEyebrow />,
      },
      {
        group: "The line above the composer",
        title: "the line that asks for the member",
        render: () => <ComposerAttention />,
      },
    ],
    props: [
      {
        of: "PromptInput",
        rows: [
          { name: "onSend", type: "(attached: File[]) => boolean | Promise<boolean>", description: "What the send does. Only the files this send took leave the card, so a file attached mid-upload is not dropped unsent." },
          { name: "...props", type: "ComponentProps<\"form\">", description: "Everything a form takes, except onSubmit. Files dropped on the card, or pasted into it, attach." },
        ],
      },
      {
        of: "PromptInputEyebrow",
        rows: [
          { name: "label", type: "string", description: "What the next turn carries. It truncates rather than widening the line." },
          { name: "tone", type: "\"default\" | \"attention\"", fallback: "\"default\"", description: "attention is the one tone that asks for the member, and the one carrying role=\"status\", so a screen reader hears it arrive." },
          { name: "glyph", type: "ReactNode", description: "A mark before the label." },
          { name: "action", type: "ReactNode", description: "An act at the end of the line." },
          { name: "onDismiss", type: "() => void", description: "Offers the way to put the line away." },
        ],
      },
      {
        of: "PromptInputSubmit",
        rows: [
          { name: "stops", type: "boolean", description: "A turn is running, so the act is drawn as a square stop. With a file attached it sends anyway." },
          { name: "busy", type: "boolean", fallback: "false", description: "The act is already in flight." },
          { name: "onStop", type: "() => void", description: "What the stop does." },
        ],
      },
      {
        of: "PromptInputAttach",
        rows: [
          { name: "(none)", type: "—", description: "It opens the card's own file chooser. The card holds what comes back, so the act takes nothing." },
        ],
      },
      {
        of: "PromptInputTextarea",
        rows: [
          { name: "...props", type: "ComponentProps<GrowingTextarea>", description: "Everything the growing textarea takes. It grows with the words, and a paste carrying files attaches them instead of writing their names." },
        ],
      },
      {
        of: "PromptInputToolbar",
        rows: [
          { name: "children", type: "ReactNode", description: "The acts under the words: the first stands left, the last right." },
        ],
      },
      {
        of: "PromptInputAttachments",
        rows: [
          { name: "(none)", type: "—", description: "It draws the files the card holds, each as a square with the act that drops it, and states the bound when a pick would take the card past ten." },
        ],
      },
      {
        of: "Attachment",
        rows: [
          { name: "size", type: "\"sm\"", fallback: "\"sm\"", description: "The card's inset and type step." },
          { name: "...props", type: "ComponentProps<\"div\">", description: "Everything a div takes." },
        ],
      },
      {
        of: "AttachmentContent",
        rows: [
          { name: "...props", type: "ComponentProps<\"div\">", description: "Everything a div takes. It holds the title and the description, and takes the width the badge leaves." },
        ],
      },
      {
        of: "AttachmentTitle",
        rows: [
          { name: "...props", type: "ComponentProps<\"span\">", description: "Everything a span takes. It truncates." },
        ],
      },
      {
        of: "AttachmentDescription",
        rows: [
          { name: "...props", type: "ComponentProps<\"span\">", description: "Everything a span takes. It truncates." },
        ],
      },
      {
        of: "AttachmentBadge",
        rows: [
          { name: "...props", type: "ComponentProps<\"span\">", description: "Everything a span takes. attachmentBadgeFor() reads the kind off a filename, and answers null for a kind that carries no badge." },
        ],
      },
      {
        of: "AttachmentGroup",
        rows: [
          { name: "...props", type: "ComponentProps<\"div\">", description: "Everything a div takes. It scrolls sideways, snaps its cards to the start, and fades at both ends." },
        ],
      },
      {
        of: "PickedThumbnail",
        rows: [
          { name: "file", type: "File", description: "The file the member picked. A picture under 10 MB is read in the browser; a document or a video under 25 MB is rastered by the preview route, pulsing until its cover arrives; anything else draws its name." },
          { name: "children", type: "ReactNode", description: "What stands over the square, such as the act that drops the file." },
          { name: "className", type: "string", description: "Layout only; the square, the border and the radius are the component's." },
        ],
      },
    ],
  },
  {
    slug: "toast",
    name: "Toast",
    modules: ["toast.tsx"],
    spread: "full",
    owns: "The note that stands over a screen after an act, where it stands, and how long it dwells before it goes.",
    examples: [
      {
        group: "States",
        title: "position=\"surface\"",
        render: () => <ToastSurface />,
      },
      {
        group: "States",
        title: "title + description",
        render: () => <ToastDescription />,
      },
      {
        group: "States",
        title: "SILENT",
        render: () => <ToastSilent />,
      },
    ],
    props: [
      {
        of: "Toast",
        rows: [
          { name: "state", type: "ToastState", description: "The title and optional description. SILENT is the empty one, and draws nothing." },
          { name: "onDone", type: "() => void", description: "Called when it has dwelt its six seconds or the member swiped it left." },
          { name: "position", type: "\"screen\" | \"surface\"", fallback: "\"screen\"", description: "Fixed at the bottom left of the whole screen, or absolute at the bottom right of the pane that acted." },
        ],
      },
    ],
  },
  {
    slug: "button",
    name: "Button",
    modules: ["button.tsx"],
    spread: "tile",
    owns: "Its ground, its border, its inset, its shape and its pressed state. A page names a variant, a size and a tone, and sets the space around it.",
    axes: ["variant", "tone", "size"],
    examples: [
      {
        group: "Variants",
        title: "variant=\"send\"",
        render: () => <ButtonSend />,
      },
      {
        group: "Variants",
        title: "variant=\"outline\"",
        render: () => <ButtonOutline />,
      },
      {
        group: "Variants",
        title: "variant=\"row\"",
        render: () => <ButtonRow />,
      },
      {
        group: "Variants",
        title: "variant=\"quiet\"",
        render: () => <ButtonQuiet />,
      },
      {
        group: "Variants",
        title: "variant=\"quiet\" pressed",
        render: () => <ButtonQuietPressed />,
      },
      {
        group: "Variants",
        title: "variant=\"option\" pressed",
        render: () => <ButtonOptionPressed />,
      },
      {
        group: "Variants",
        title: "variant=\"mark\"",
        render: () => <ButtonMark />,
      },
      {
        group: "Variants",
        title: "variant=\"corner\"",
        render: () => <ButtonCorner />,
      },
      {
        group: "Sizes",
        title: "size=\"default\"",
        render: () => <ButtonSizeDefault />,
      },
      {
        group: "Sizes",
        title: "size=\"commit\"",
        render: () => <ButtonSizeCommit />,
      },
      {
        group: "Sizes",
        title: "size=\"bar\"",
        render: () => <ButtonSizeBar />,
      },
      {
        group: "Sizes",
        title: "size=\"chip\"",
        render: () => <ButtonSizeChip />,
      },
      {
        group: "Sizes",
        title: "size=\"icon\"",
        render: () => <ButtonSizeIcon />,
      },
      {
        group: "Sizes",
        title: "size=\"glyph\"",
        render: () => <ButtonSizeGlyph />,
      },
      {
        group: "Tones",
        title: "tone=\"soft\"",
        render: () => <ButtonToneSoft />,
      },
      {
        group: "Tones",
        title: "tone=\"attention\"",
        render: () => <ButtonToneAttention />,
      },
      {
        group: "States",
        title: "busy",
        render: () => <ButtonBusy />,
      },
      {
        group: "States",
        title: "disabled",
        render: () => <ButtonDisabled />,
      },
    ],
    props: [
      {
        of: "Button",
        rows: [
          { name: "variant", type: "\"send\" | \"outline\" | \"row\" | \"quiet\" | \"mark\" | \"option\" | \"corner\"", fallback: "\"outline\"", description: "The ground, the border and the pressed state." },
          { name: "size", type: "\"default\" | \"commit\" | \"bar\" | \"chip\" | \"icon\" | \"glyph\"", fallback: "\"default\"", description: "The box the act is drawn in." },
          { name: "tone", type: "\"soft\" | \"attention\"", fallback: "—", description: "The ink, over whatever the variant sets." },
          { name: "busy", type: "boolean", fallback: "false", description: "The act is in flight: it fades between 0.6 and 0.35 opacity every 1.4 seconds, keeps its place in the accessibility tree and swallows a second press, where disabled would drop the focused element out of it mid-submit." },
          { name: "strong", type: "boolean", fallback: "false", description: "The label at the strong weight: ConfirmButton carries it while armed, so the press that commits does not read as the press that armed it." },
          { name: "...props", type: "ComponentProps<\"button\">", description: "Everything a button takes. type is \"button\" unless a caller states otherwise." },
        ],
      },
      {
        of: "ConfirmButton",
        rows: [
          { name: "verb", type: "string", description: "The act. The first press arms the button and it reads Confirm <verb>; the second takes the act, and a blur disarms it." },
          { name: "...props", type: "ButtonProps", description: "Everything Button takes." },
        ],
      },
      {
        of: "buttonVariants",
        rows: [
          { name: "{ variant, size, tone }", type: "string", description: "The class recipe, for an element that must read as a button and cannot be one — a download link, a label, or a primitive that draws its own element." },
        ],
      },
    ],
  },
];
