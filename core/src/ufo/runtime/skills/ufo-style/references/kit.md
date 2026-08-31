# The kit

Every component `ufo/kit` publishes, with what it is in the words of the file that defines it.
Generated from `kit.ts` at build time — a name here is a name the kit exports, and a name absent
here is not in the kit whatever it is called elsewhere.

Reach for one of these before composing a shape out of `div`s: a page built from them answers to
the theme, both colour schemes and every width at once, and a shape built beside them does not.

## Chat

- **`FoundingChat`** — A screen standing over the box that founds a conversation:

## ChatPane

- **`ChatPane`** — The chat screen for one agent's conversation:

## Conversations

- **`ConversationDetail`** — One conversation's transcript, read from the agent's transcript route, with a way back where the pane above has none.

## action

- **`ApplicationAction`** — Apply one connector-supplied prepared action and show its durable result.

## agentIcon

- **`AgentIcon`** — One app's mark, drawn at `--size-glyph` unless its caller sets another size, in the ink of whatever it sits in.

## agentName

- **`agentName`** — An agent's name as a member reads it:

## api

- **`getJson`** — A GET of the portal API, answered as `{ok, payload}` or `{ok:
- **`postIntent`** — Post a prepared intent to an agent — the one mutation path a page has; the turn is the chat transport and the audit record.

## artifact

- **`ArtifactText`** — A shared text file, read to the fold.
- **`FileSheet`** — A shared file opened in a Sheet:
- **`MediaIcon`** — What a file with no picture of its own is drawn as:
- **`isTextMedia`** — Whether a media type is text the page can show as text — `text/*` and JSON.
- **`useTextArtifact`** — A shared text file's characters, read to the byte the fold is cut at:

## audience

- **`isMemberAudience`** — Whether a wire audience names one member rather than a room, the workspace, or another org.
- **`isPortalChat`** — Whether a conversation's surface is the portal — the web surface or an extension's.
- **`ownerLabel`** — Whose a record is:
- **`slackLink`** — Where a conversation leads back out to in Slack:
- **`surfaceWord`** — The member's word for a surface.
- **`useViewer`** — The signed-in member's email, from the provider mountApp installs.

## avatar

- **`Avatar`** — The circle a member's initial and an app's mark are drawn in, at the one `--size-avatar` the portal gives them both:
- **`AvatarFallback`** — What the circle holds.

## avatar-stack

- **`AvatarStack`** — The faces of the members a row or a card is about, overlapping by the one measure the stack spells, with everyone past the third stated as a count.

## badge

- **`Badge`** — The state a record is in, drawn beside the line that names it:

## bands

- **`AppConversations`** — What the app has done:

## brandMark

- **`BrandMark`** — A provider's mark where the portal offers it, drawn round:

## breakdown

- **`Breakdown`** — What a measure is made of, as a column of named parts each carrying its share:
- **`BreakdownHeader`** — The line over the column:
- **`BreakdownLabel`** — What the parts are parts of, cut at the line's width so the act beside it keeps its end.
- **`BreakdownMark`** — The mark before the name, held at the glyph square whatever it is given, so no column states that size itself and one oversized mark cannot set the row's height.
- **`BreakdownName`** — The part's mark and its name, read as one thing at the row's leading edge.
- **`BreakdownRow`** — One part and its share, held apart so the figure lands on the column's right edge.
- **`BreakdownRows`** — The shares themselves, at their own pitch rather than the column's:
- **`BreakdownValue`** — The part's share.

## button

- **`Button`** — A button lays its content out as a centred row, so a glyph sits in the middle of the box rather than on the text baseline at its left edge, and `size="icon"` is the box a glyph alone is drawn in:
- **`buttonVariants`** — The button's class recipe by `variant` and `size`, for an element that must read as a Button and cannot be one — a download link, a label.

## card

- **`Card`** — A record drawn as its own panel:
- **`CardAction`** — What the card offers at the end of the head's line — a badge, a glyph, a menu.
- **`CardContent`** — What the card is about, whatever that is — a table, a list of rows, a card of its own.
- **`CardDescription`** — The card's prose, and the one part of it that grows:
- **`CardFooter`** — The card's foot:
- **`CardHeader`** — `rows` is the card as a ruled list:
- **`CardTitle`** — What the card is, cut at the head's width rather than wrapped:

## cards

- **`CardGrid`** — A grid of cards holds its rhythm only while every card is the same height, so the text a row supplies is cut to a fixed number of lines:

## chart

- **`Chart`** — A measure's shape over the period it was measured, drawn as one series and the wash beneath it:
- **`ChartBars`** — A count per period, drawn as one column each against the track it could have filled:

## cn

- **`cn`** — The spacing, radius and type scales are named by role, so tailwind-merge cannot recognise `px-3xl` as a padding, `rounded-panel` as a radius or `text-label` as a size without being told the names.

## detail

- **`Detail`** — One aspect of a subject stated at length:

## dialog

- **`Dialog`** — A modal dialog's root:
- **`DialogTrigger`** — The control that opens the Dialog it stands in; `asChild` makes the child the trigger.

## dropdown-menu

- **`DropdownMenu`** — A menu's root:
- **`DropdownMenuCheckboxItem`** — A choice a member turns on and off rather than picks between.
- **`DropdownMenuContent`** — `container` is where the menu is drawn:
- **`DropdownMenuItem`** — One act in a menu:
- **`DropdownMenuLabel`** — The name over the items it heads.
- **`DropdownMenuRadioGroup`** — A set of DropdownMenuRadioItem rows of which one is chosen — a sort, an order.
- **`DropdownMenuRadioItem`** — One choice in a DropdownMenuRadioGroup, ticked when it is the one chosen.
- **`DropdownMenuSeparator`** — The rule between two groups of items.
- **`DropdownMenuTrigger`** — The control that opens the DropdownMenu it stands in; `asChild` makes the child the trigger.

## facts

- **`Facts`** — What a pane states about its own subject:
- **`Group`** — `action` is what the group does to itself, standing at the far end of its own name:

## filter

- **`Segmented`** — The one row of choices in the portal, whether it narrows a listing or switches a page's panel.

## legend

- **`Legend`** — What the colours in a graphic stand for, as a row of named swatches under it.
- **`LegendItem`** — One tone and the name it stands for.

## mainAgent

- **`useAgents`** — Every agent the viewer's web audience holds.
- **`useMainAgent`** — The workspace's main agent, or null before the roster arrives.

## markdown

- **`Markdown`** — A settled document:

## meter

- **`Meter`** — How a whole divides, drawn as one bar:

## moments

- **`Moment`** — A record's time wherever the portal draws one:
- **`day`** — The one way a date reads in this portal:

## objects

- **`ObjectDetail`** — One object's record inside a sheet.
- **`ObjectPane`** — One object kind's index and selected record sheet.
- **`creator`** — Who made a row, in the member's words — the wire's `owner_email` never renders raw.
- **`objectAt`** — The object named by a route id, or null when the id names another kind of view.
- **`slotOf`** — The route id for one object in one app namespace.

## pager

- **`Pager`** — The Newer and Older steps under a listing, drawn only for the cursors the payload holds.

## pane

- **`BANDS`** — The one gap a stack of bands is set at, and the only place it is written.
- **`COLUMN`** — The one measure the portal is read at, centred in whatever width the shell leaves — a transcript, and a screen of records alike.
- **`FacetMenu`** — Every narrowing a listing offers, behind one glyph.
- **`Header`** — The band every surface is headed by:
- **`Page`** — Every screen is this:
- **`Pane`** — The pane a destination draws in.
- **`PageToolbar`** — The band that narrows the records:
- **`PaneNote`** — A sentence standing where a screen could not draw:
- **`ToolbarRule`** — The rule between the controls that narrow a listing and the controls that redraw it.
- **`ViewSwitch`** — Which shape the records are drawn in, held at the right of the bar where the acts on the whole listing stand.
- **`usePageHead`** — Puts a view's own band above the shell it is drawn in.

## panel

- **`Empty`** — A sentence standing where records would:
- **`Panel`** — Draws a read by its phase:
- **`PanelBlank`** — A section that holds no records yet takes a card on the section's own left edge.
- **`PanelEmpty`** — The empty sentence at a section's own height:
- **`Section`** — A band of records inside a page, stacked at the page's own rhythm:
- **`Waiting`** — The one line a screen states while it has nothing else, and the only place the words are written.
- **`usePanelRead`** — A read holds its answer until the next one lands.

## pressrow

- **`PressRow`** — A row that opens something.

## rebuild

- **`RebuildDialog`** — Asking a job to write a page's text again.

## route

- **`agentHash`** — The address of an agent's screen at a place.
- **`agentSetupHash`** — The address of an agent's setup screen.
- **`chatHash`** — The address of one conversation, optionally at a slot.
- **`conversationSlotHash`** — The address of one slot in a conversation.
- **`homeHash`** — The address of the workspace home at a place.
- **`newChatHash`** — The address of a fresh chat with an agent.
- **`parseHash`** — What the address says, read off the table.
- **`routeIs`** — Whether a route is of a kind, and the narrowing that goes with it.
- **`sectionHash`** — The address of a section, optionally at a place.
- **`workspaceHash`** — The address of a workspace tab, optionally at a place.

## rows

- **`RowLines`** — A list of records read as one card of ruled rows:

## runtime

- **`compose`** — Hand words to the composer of a new chat with this app, unsent.
- **`connect`** — The handshake:
- **`founded`** — Tell the shell a send on this page founded a conversation, so its rail carries the row without waiting for the next read.
- **`installShims`** — Reroute the page's portal transport:
- **`navigate`** — Move the portal to an address:
- **`onPlaced`** — The pane's place as it changes while the page stands — the live half of `init`'s `place`.

## separator

- **`Separator`** — The rule between two stretches of one screen — a band and the rows beneath it, a group of acts and the next.

## sheet

- **`Sheet`** — A drawer on the right edge that opens over the pane without taking the screen:

## shell

- **`mountApp`** — Mount an app page:
- **`SectionApp`** — One section screen standing as the whole page, its place in page state and seeded by the place the pane was opened at — the whole place, so the screen inside the frame stands where the address outside it says.
- **`useAppLinks`** — Route every in-page link:

## size

- **`formatSize`** — A byte count as a member reads it.

## slots

- **`appended`** — The track after a deliberate open-beside:
- **`beside`** — Whether a press asked for its target to stand beside the track rather than replace the path under the lane it was raised in.
- **`closed`** — The track after `id` is shut, which shuts what was opened from it:
- **`opened`** — The track after an act taken in `from` opened `id`:

## stat

- **`Stat`** — One measure drawn as a tile:
- **`StatDelta`** — How the measure moved, drawn beside the figure it moved.
- **`StatDescription`** — The rule the figure was counted by, wrapped and given the whole width:
- **`StatHeader`** — The line over the figure:
- **`StatLabel`** — What is counted, cut at the line's width rather than wrapped:
- **`StatMedia`** — A mark on the header line — the glyph of the connector a measure was counted from, an app's avatar.
- **`StatValue`** — The figure, and beside it the move it made:

## surfaceMark

- **`SurfaceGlyph`** — The surface a conversation came in on, drawn at the far end of its row in a listing of them.

## table

- **`Lede`** — What a record's first cell holds:
- **`Td`** — One table cell:
- **`TdFact`** — A column holding one short fact the eye compares straight down — a model id, a state.
- **`DataTable`** — `note` is what a *narrowed* table says when nothing is left:

## Also published

Named without description — the types, and the React and Tabler exports a page imports from the
kit rather than from a global, which a framed page does not have.

`Agent`, `ApplicationActionRecord`, `AppInit`, `AvatarStackPerson`, `ChartBar`, `MeterPart`, `ChatRow`, `Conversation`, `Crumb`, `Face`, `FacetGroup`, `Member`, `ObjectAddress`, `ObjectRow`, `PaneView`, `PanelState`, `Placement`, `ReactMouseEvent`, `ReactNode`, `RefObject`, `SharedFile`, `WorkspacePlace`, `React`, `useCallback`, `useEffect`, `useId`, `useLayoutEffect`, `useMemo`, `useRef`, `useState`, `IconChevronDown`, `IconChevronUp`, `IconDots`, `IconFilter2`, `IconWorldWww`, `IconX`.
