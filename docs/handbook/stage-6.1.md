# Browser Web Portal Surface  `stage-6.1`

This stage is the browser-facing front door of the system. It is part of the live main interface: the place where a member signs in, sees agents, chats, changes settings, and opens pages like memory, usage, connections, objects, admin views, and live streams.

The main web surface serves the browser app and protects it with a session cookie, which is a small browser-stored sign-in token. It also provides the routes the app uses to read data, send chat messages, and make changes. The audience code acts like a door attendant. It decides which members may see or chat with which agents, and gives admins tools to grant access or inspect private transcripts. Panels turns setup and settings form submissions into ordinary agent tool calls, so button clicks fit the same system as chat actions. Starters prepares the suggested prompts on the start screen, using remembered work and short-term caching to stay fast. Community reads public skills from skills.sh and presents errors clearly. The memory surface lets authorized operators inspect saved workspace memory. The chat package marker simply makes the chat extension importable.

## Files in this stage

### Portal shell and chat package
The browser portal entrypoint serves the authenticated web app and chat/API surface, while the chat package marker makes the app chat extension importable.

### `extensions/web/ufo_ext_web/surface.py`

`io_transport` · `startup, request handling, live streaming, scheduled background jobs`

Think of this file as the front desk for UFO’s web product. A browser arrives with a signed session cookie, this file checks who the member is, works out which workspace and agents they are allowed to see, and then answers the page’s requests. It serves the built web app and its static files, including fallbacks for assets from older deployments so a rolling deploy does not break pages already open in someone’s browser.

Most of the file is route logic. Some routes read information: agent lists, live agent status, transcripts, memory, sources, credentials, usage, settings, skills, connections, and generic object lists. Other routes perform controlled actions: opening a session, sending a chat message, stopping a turn, fulfilling a credential prompt, following a connection handoff, applying prepared intents, or writing objects from an app frame.

The important safety pattern is repeated everywhere: authenticate first, resolve the member’s web audience, then check the specific agent, conversation, object, or turn before returning anything. Conversation rendering is also centralized here, so chat, transcripts, history pages, attachments, subagent runs, shared files, created apps, questions, and connection prompts are shown consistently. The file is large because it is the seam where the browser-facing portal translates core UFO concepts into simple JSON and server-sent events for the UI.

#### Function details

##### `load_assets`  (lines 228–238)

```
def load_assets(directory: Path) -> dict[str, tuple[bytes, str]]
```

*Call graph*: 1 external calls (glob).


##### `rum_config`  (lines 251–264)

```
def rum_config(environ: Mapping[str, str]) -> dict[str, str] | None
```

*Call graph*: called by 1 (portal_page).


##### `portal_shell`  (lines 267–276)

```
def portal_shell(html: str, config: Mapping[str, str] | None) -> str
```

*Call graph*: called by 1 (portal_page); 1 external calls (dumps).


##### `resolve_workspace`  (lines 289–328)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None
```

*Call graph*: calls 3 internal fn (_chat_target, _form, _framed_length); 2 external calls (workspace_claim, RedirectResponse).


##### `_chat_target`  (lines 331–335)

```
def _chat_target(request: Request) -> UUID | None
```

*Call graph*: called by 1 (resolve_workspace); 1 external calls (UUID).


##### `_static_response`  (lines 338–348)

```
def _static_response(request: Request) -> Response | None
```

*Call graph*: calls 1 internal fn (_asset_response); called by 1 (static_asset).


##### `_asset_response`  (lines 351–355)

```
def _asset_response(request: Request, body: bytes, media_type: str, etag: str) -> Response
```

*Call graph*: called by 2 (_static_response, _stored_asset); 1 external calls (Response).


##### `load_apps`  (lines 381–414)

```
def load_apps(directory: Path) -> AppsBundle | None
```

*Call graph*: 4 external calls (__init__, sha256, is_dir, rglob).


##### `apps`  (lines 420–426)

```
def apps() -> AppsBundle
```

*Call graph*: called by 3 (_homepage_state, agents_index, homepage).


##### `_publish_assets`  (lines 429–439)

```
async def _publish_assets(blob: BlobStore, apps: AppsBundle) -> None
```

*Call graph*: calls 3 internal fn (exists, list, put); called by 1 (_assets_published).


##### `_assets_published`  (lines 442–469)

```
def _assets_published(blob: BlobStore, apps: AppsBundle) -> 'asyncio.Task[None]'
```

*Call graph*: calls 1 internal fn (_publish_assets); called by 3 (agents_index, homepage, portal_page); 1 external calls (create_task).


##### `_stored_asset`  (lines 472–494)

```
async def _stored_asset(blob: BlobStore, request: Request) -> Response
```

*Call graph*: calls 3 internal fn (exists, get, _asset_response); called by 1 (static_asset); 3 external calls (sha256, Path, Response).


##### `portal_page`  (lines 497–514)

```
async def portal_page(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 3 internal fn (_assets_published, portal_shell, rum_config); 1 external calls (HTMLResponse).


##### `_authenticate`  (lines 517–535)

```
async def _authenticate(ctx: SurfaceContext, request: Request) -> tuple[UUID, str] | Response
```

*Call graph*: calls 2 internal fn (link_member, linked_member); called by 2 (_audience_for, fulfill_credential); 2 external calls (verify_token, Response).


##### `static_asset`  (lines 538–544)

```
async def static_asset(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 2 internal fn (_static_response, _stored_asset).


##### `open_session`  (lines 547–578)

```
async def open_session(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 2 internal fn (_form, _framed_length); 3 external calls (JSONResponse, RedirectResponse, set_session_cookie).


##### `_agent_param`  (lines 581–585)

```
def _agent_param(request: Request) -> UUID | None
```

*Call graph*: called by 4 (_member_chat_page, _panel_gate, chat, transcript); 1 external calls (UUID).


##### `_chat_row_key`  (lines 588–589)

```
def _chat_row_key(conversation_id: UUID) -> str
```

*Call graph*: called by 2 (_open_conversation, _own_web_chat).


##### `_chat_title`  (lines 598–616)

```
def _chat_title(text: str, paths: tuple[str, ...]) -> str
```

*Call graph*: called by 2 (_open_conversation, summarize_chat_titles).


##### `_title_excerpt`  (lines 628–646)

```
def _title_excerpt(messages: tuple[Message, ...]) -> str
```

*Call graph*: calls 1 internal fn (_rendered_text); called by 1 (summarize_chat_titles); 1 external calls (member_message_text).


##### `summarize_chat_titles`  (lines 649–695)

```
async def summarize_chat_titles(ctx: ExtensionContext) -> None
```

*Call graph*: calls 4 internal fn (conversations_awaiting_title, summarized_conversation_title, _chat_title, _title_excerpt); 2 external calls (__init__, __init__).


##### `seed_homepages`  (lines 698–769)

```
async def seed_homepages(ctx: ExtensionContext, bucket: str | None=None) -> None
```

*Call graph*: calls 5 internal fn (earliest_seated_admin, invoke, open_conversation, turn_outcomes, workspace_agents); 2 external calls (now, shipped_app_slug).


##### `_open_conversation`  (lines 772–805)

```
async def _open_conversation(ctx: SurfaceContext, store: ScopedStore, agent_id: UUID, member_id: UUID, email: str, queue_key: str, text: str, paths: tuple[str, ...]) -> tuple[UUID, str]
```

*Call graph*: calls 8 internal fn (delete, put, conversation_for, retitle_conversation, _chat_row_key, _chat_title, _named, _own_web_chat); called by 1 (chat); 3 external calls (__init__, conversation_audience, uuid4).


##### `_named`  (lines 808–815)

```
async def _named(ctx: SurfaceContext, agent_id: UUID, member_id: UUID, conversation_id: UUID) -> str
```

*Call graph*: calls 1 internal fn (list_agent_conversations); called by 1 (_open_conversation).


##### `_own_web_chat`  (lines 818–830)

```
async def _own_web_chat(store: ScopedStore, agent_id: UUID, email: str, conversation_id: UUID) -> ChatRecord | None
```

*Call graph*: calls 2 internal fn (get, _chat_row_key); called by 2 (_member_chat, _open_conversation).


##### `_member_chat`  (lines 833–878)

```
async def _member_chat(ctx: SurfaceContext, store: ScopedStore, agent_id: UUID, member_id: UUID, email: str, conversation_id: UUID, *, agent_visible: bool) -> ListedConversation | None
```

*Call graph*: calls 3 internal fn (list_agent_conversations, _commentable, _own_web_chat); called by 5 (_member_chat_page, _member_turn, _resolve_chat, chat, transcript); 1 external calls (conversation_audience).


##### `_commentable`  (lines 881–885)

```
def _commentable(conversation: ListedConversation, member_id: UUID) -> bool
```

*Call graph*: called by 5 (_conversation_row, _member_chat, _member_turn, _resolve_chat, chat); 1 external calls (conversation_audience).


##### `_turn_context`  (lines 888–899)

```
def _turn_context(email: str, request: Request, source: str) -> TurnContext
```

*Call graph*: called by 1 (chat); 2 external calls (__init__, log).


##### `_chat_url`  (lines 902–905)

```
def _chat_url(public_base_url: str | None, conversation_id: UUID) -> str | None
```

*Call graph*: called by 2 (_chat_source, _comment_notice).


##### `_chat_source`  (lines 908–917)

```
def _chat_source(public_base_url: str | None, conversation_id: UUID, email: str) -> str
```

*Call graph*: calls 1 internal fn (_chat_url); called by 1 (chat).


##### `_comment_notice`  (lines 920–940)

```
def _comment_notice(public_base_url: str | None, conversation: ListedConversation, member_id: UUID, email: str, text: str, paths: tuple[str, ...]) -> str
```

*Call graph*: calls 1 internal fn (_chat_url); called by 1 (chat); 2 external calls (PurePosixPath, conversation_audience).


##### `_audience_for`  (lines 943–950)

```
async def _audience_for(ctx: SurfaceContext, request: Request) -> tuple[UUID, str, WebAudience] | Response
```

*Call graph*: calls 1 internal fn (_authenticate); called by 24 (_member_chat_page, _member_turn, _object_gate, _panel_gate, admin_index, agents_index, agents_status, chat, chats_index, connection_pool (+14 more)); 2 external calls (web_audience, web_extension).


##### `_visibility_flag`  (lines 971–975)

```
def _visibility_flag(agent: AgentSummary) -> str | None
```

*Call graph*: called by 2 (_flag_reads, agents_index); 1 external calls (shipped_app_slug).


##### `_flag_reads`  (lines 978–998)

```
async def _flag_reads(agents: tuple[AgentSummary, ...]) -> dict[str, bool]
```

*Call graph*: calls 1 internal fn (_visibility_flag); called by 1 (agents_index); 2 external calls (gather, flag_enabled).


##### `_setup_ready`  (lines 1001–1011)

```
def _setup_ready(state: SetupState) -> bool
```

*Call graph*: called by 2 (agents_index, workspace_starters).


##### `agents_index`  (lines 1014–1112)

```
async def agents_index(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 8 internal fn (list_archived_agents, _assets_published, _audience_for, _flag_reads, _homepage_state, _setup_ready, _visibility_flag, apps); 6 external calls (Semaphore, gather, JSONResponse, shipped_app_slug, granted_emails, web_extension).


##### `agents_index.setup_of`  (lines 1056–1058)

```
async def setup_of(agent: AgentSummary) -> SetupState
```


##### `agents_status`  (lines 1115–1156)

```
async def agents_status(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 4 internal fn (agent_turn_statuses, latest_activity, _audience_for, _iso); 1 external calls (JSONResponse).


##### `_framed_length`  (lines 1159–1172)

```
def _framed_length(request: Request, limit: int) -> Response | None
```

*Call graph*: called by 5 (_parse_inbound, fulfill_credential, open_session, preview, resolve_workspace); 1 external calls (Response).


##### `_form`  (lines 1175–1182)

```
async def _form(request: Request) -> FormData | Response
```

*Call graph*: called by 5 (_parse_inbound, fulfill_credential, open_session, preview, resolve_workspace); 2 external calls (form, Response).


##### `_bounded_body`  (lines 1185–1193)

```
async def _bounded_body(request: Request, limit: int) -> bytes | Response
```

*Call graph*: called by 1 (_parse_inbound); 2 external calls (stream, Response).


##### `_parse_inbound`  (lines 1196–1232)

```
async def _parse_inbound(request: Request) -> tuple[str, tuple[UploadFile, ...]] | Response
```

*Call graph*: calls 3 internal fn (_bounded_body, _form, _framed_length); called by 1 (chat); 1 external calls (Response).


##### `_inbox_paths`  (lines 1235–1239)

```
def _inbox_paths(uploads: tuple[UploadFile, ...]) -> tuple[str, ...]
```

*Call graph*: called by 1 (chat); 1 external calls (inbox_name).


##### `_deliver_uploads`  (lines 1242–1251)

```
async def _deliver_uploads(ctx: SurfaceContext, conversation_id: UUID, uploads: tuple[UploadFile, ...], paths: tuple[str, ...]) -> None
```

*Call graph*: calls 2 internal fn (write_workspace_file, _upload_chunks); called by 1 (chat).


##### `_files_note`  (lines 1254–1257)

```
def _files_note(text: str, paths: tuple[str, ...]) -> str
```

*Call graph*: called by 1 (chat).


##### `_member_attachments`  (lines 1265–1273)

```
def _member_attachments(said: str) -> tuple[str, tuple[str, ...]]
```

*Call graph*: called by 1 (_member_bubble).


##### `_attachment_preview`  (lines 1276–1288)

```
def _attachment_preview(public_base_url: str | None, agent_id: UUID, conversation_id: UUID, path: str) -> str | None
```

*Call graph*: 2 external calls (raster_image_media_type, quote).


##### `_attachment_payload`  (lines 1291–1304)

```
def _attachment_payload(path: str, preview_url: str | None) -> dict[str, object]
```

*Call graph*: called by 1 (_member_bubble); 2 external calls (PurePosixPath, raster_image_media_type).


##### `_member_bubble`  (lines 1307–1315)

```
def _member_bubble(said: str, attach: Attach | None) -> dict[str, object]
```

*Call graph*: calls 2 internal fn (_attachment_payload, _member_attachments); called by 2 (_conversation_messages, _rendered_messages).


##### `_upload_chunks`  (lines 1318–1320)

```
async def _upload_chunks(upload: UploadFile) -> AsyncIterator[bytes]
```

*Call graph*: called by 1 (_deliver_uploads); 1 external calls (read).


##### `_answer_key`  (lines 1323–1328)

```
def _answer_key(conversation_id: UUID, turn_id: UUID, index: int) -> str
```

*Call graph*: called by 2 (_asks, chat).


##### `_answer_headers`  (lines 1331–1343)

```
def _answer_headers(request: Request) -> tuple[UUID, int] | None | Response
```

*Call graph*: called by 1 (chat); 2 external calls (Response, UUID).


##### `_stop_header`  (lines 1346–1355)

```
def _stop_header(request: Request) -> UUID | None | Response
```

*Call graph*: called by 1 (chat); 2 external calls (Response, UUID).


##### `chat`  (lines 1358–1474)

```
async def chat(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 19 internal fn (admit, admitted_body, stop_turn, _agent_param, _answer_headers, _answer_key, _audience_for, _chat_source, _comment_notice, _commentable (+9 more)); 5 external calls (JSONResponse, Response, web_extension, UUID, uuid4).


##### `_rendered_text`  (lines 1477–1488)

```
def _rendered_text(message: Message) -> str
```

*Call graph*: called by 2 (_rendered_messages, _title_excerpt).


##### `_append_activity`  (lines 1491–1492)

```
def _append_activity(events: list[dict[str, str]], text: str) -> None
```

*Call graph*: called by 2 (_rendered_messages, _subagent_activity).


##### `_stored_activity`  (lines 1495–1507)

```
def _stored_activity(block: ToolUseBlock, result: ToolResultBlock) -> str | None
```

*Call graph*: called by 2 (_rendered_messages, _subagent_activity).


##### `_subagent_activity`  (lines 1529–1553)

```
def _subagent_activity(messages: tuple[Message, ...]) -> list[dict[str, str]]
```

*Call graph*: calls 2 internal fn (_append_activity, _stored_activity); called by 1 (_subagent_nodes).


##### `_finish_payload`  (lines 1556–1566)

```
def _finish_payload(answer: str) -> dict[str, JsonValue] | None
```

*Call graph*: called by 1 (_run_answer); 1 external calls (loads).


##### `_payload_prose`  (lines 1569–1592)

```
def _payload_prose(value: JsonValue) -> str
```

*Call graph*: called by 1 (_run_answer); 2 external calls (items, strip).


##### `_run_answer`  (lines 1595–1611)

```
def _run_answer(answer: str) -> str
```

*Call graph*: calls 2 internal fn (_finish_payload, _payload_prose); called by 2 (render, _subagent_nodes).


##### `_subagent_nodes`  (lines 1614–1653)

```
async def _subagent_nodes(ctx: SurfaceContext, turns: tuple[Turn, ...]) -> SubagentRuns
```

*Call graph*: calls 4 internal fn (list_agents, read_transcript, _run_answer, _subagent_activity); called by 2 (_events, _transcript_aids); 2 external calls (__init__, gather).


##### `_rendered_messages`  (lines 1656–1819)

```
def _rendered_messages(messages: tuple[Message, ...], subagents: SubagentRuns | None=None, turn_ids: frozenset[str]=frozenset(), agent_origin: frozenset[str]=frozenset(), speakers: Mapping[str, str] |
```

*Call graph*: calls 4 internal fn (_append_activity, _member_bubble, _rendered_text, _stored_activity); called by 1 (render); 1 external calls (member_message_text).


##### `_rendered_messages.note_answer`  (lines 1731–1736)

```
def note_answer() -> None
```


##### `_rendered_messages.flush_reply`  (lines 1738–1773)

```
def flush_reply(include_subagents: bool) -> None
```


##### `_asks`  (lines 1838–1870)

```
def _asks(conversation_id: UUID, turns: tuple[Turn, ...], admitted: tuple[KeyedAdmission, ...]) -> _Asks
```

*Call graph*: calls 1 internal fn (_answer_key); called by 1 (_transcript_aids); 2 external calls (__init__, member_message_text).


##### `_TranscriptAids.render`  (lines 1893–1912)

```
def render(self, messages: tuple[Message, ...]) -> list[dict[str, object]]
```

*Call graph*: calls 2 internal fn (_rendered_messages, _run_answer).


##### `_transcript_aids`  (lines 1915–1967)

```
async def _transcript_aids(ctx: SurfaceContext, agent_id: UUID, conversation_id: UUID, viewer: UUID, agent_origin: frozenset[str], speakers: dict[str, str], asked: dict[str, str], opens: frozenset[UUI
```

*Call graph*: calls 9 internal fn (conversation_subagent_turns, keyed_admissions, list_conversation_artifacts, list_turns, _asks, _connect_controls, _created_apps, _file_payload, _subagent_nodes); called by 2 (_conversation_messages, _history_messages); 3 external calls (__init__, gather, partial).


##### `_connect_controls`  (lines 1970–2010)

```
async def _connect_controls(ctx: SurfaceContext, conversation_id: UUID, turns: tuple[Turn, ...], viewer: UUID) -> dict[str, dict[str, object]]
```

*Call graph*: calls 4 internal fn (connect_available, held_accounts, _connect_control, _provider_label); called by 1 (_transcript_aids).


##### `_conversation_messages`  (lines 2013–2135)

```
async def _conversation_messages(ctx: SurfaceContext, agent_id: UUID, conversation_id: UUID, viewer: UUID, opens: frozenset[UUID]) -> tuple[list[dict[str, object]], Turn | None, int]
```

*Call graph*: calls 10 internal fn (agent_origin_refs, arrival_speakers, latest_turn, list_compactions, queued_arrivals, read_transcript, turn_detail, _member_bubble, _transcript_aids, _verified_earlier); called by 2 (conversation_transcript, transcript); 3 external calls (gather, partial, member_message_text).


##### `_verified_earlier`  (lines 2138–2154)

```
async def _verified_earlier(ctx: SurfaceContext, conversation_id: UUID, indices: tuple[int, ...], messages: tuple[Message, ...]) -> int
```

*Call graph*: calls 1 internal fn (read_compaction_after); called by 2 (_conversation_messages, _history_messages).


##### `_history_cursor`  (lines 2157–2159)

```
def _history_cursor(index: int, end: int | None=None) -> str
```

*Call graph*: called by 4 (fits, _history_messages, conversation_transcript, transcript); 1 external calls (urlsafe_b64encode).


##### `_history_position`  (lines 2162–2177)

```
def _history_position(cursor: str) -> tuple[int, int | None]
```

*Call graph*: called by 1 (_history_messages); 1 external calls (b64decode).


##### `_bounded_history_page`  (lines 2180–2202)

```
def _bounded_history_page(messages: list[dict[str, object]], index: int, end: int) -> tuple[list[dict[str, object]], int]
```

*Call graph*: called by 1 (_history_messages).


##### `_bounded_history_page.fits`  (lines 2185–2190)

```
def fits(start: int) -> bool
```

*Call graph*: calls 1 internal fn (_history_cursor); 1 external calls (JSONResponse).


##### `_history_messages`  (lines 2205–2256)

```
async def _history_messages(ctx: SurfaceContext, agent_id: UUID, conversation_id: UUID, viewer: UUID, cursor: str, opens: frozenset[UUID]) -> tuple[list[dict[str, object]], str | None] | None
```

*Call graph*: calls 8 internal fn (agent_origin_refs, arrival_speakers, read_compaction, _bounded_history_page, _history_cursor, _history_position, _transcript_aids, _verified_earlier); called by 1 (_conversation_history); 1 external calls (gather).


##### `transcript`  (lines 2259–2303)

```
async def transcript(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 7 internal fn (_agent_param, _audience_for, _conversation_messages, _history_cursor, _member_chat, _open_handoffs, _opens); 4 external calls (JSONResponse, Response, web_extension, UUID).


##### `_open_handoffs`  (lines 2306–2317)

```
async def _open_handoffs(ctx: SurfaceContext, terminal: TerminalFrame) -> dict[str, object]
```

*Call graph*: calls 1 internal fn (_pending_prompts); called by 1 (transcript).


##### `_connect_control`  (lines 2320–2324)

```
def _connect_control(ctx: SurfaceContext, provider: str, turn_id: UUID) -> dict[str, object]
```

*Call graph*: calls 1 internal fn (_provider_label); called by 2 (_connect_controls, _events).


##### `_provider_label`  (lines 2327–2338)

```
def _provider_label(ctx: SurfaceContext, provider: str) -> str
```

*Call graph*: calls 2 internal fn (connect_available, connect_label); called by 3 (_connect_control, _connect_controls, agent_setup).


##### `_provider_summary`  (lines 2341–2348)

```
def _provider_summary(provider: str) -> str
```

*Call graph*: called by 1 (agent_setup).


##### `chats_index`  (lines 2351–2363)

```
async def chats_index(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 2 internal fn (_audience_for, _resolve_chat); 2 external calls (Response, web_extension).


##### `_resolve_chat`  (lines 2366–2454)

```
async def _resolve_chat(ctx: SurfaceContext, store: ScopedStore, audience: WebAudience, member_id: UUID, email: str, requested: str) -> Response
```

*Call graph*: calls 9 internal fn (conversation_agent, latest_turn, list_agent_conversations, turn_detail, allows, _commentable, _conversation_row, _iso, _member_chat); called by 1 (chats_index); 2 external calls (JSONResponse, UUID).


##### `_panel_gate`  (lines 2457–2470)

```
async def _panel_gate(ctx: SurfaceContext, request: Request) -> tuple[UUID, str, WebAudience, UUID] | Response
```

*Call graph*: calls 2 internal fn (_agent_param, _audience_for); called by 10 (_readable_conversation, agent_setup, community_skill, community_skills, connections, conversations, homepage, intents, settings, skills); 1 external calls (Response).


##### `_iso`  (lines 2473–2474)

```
def _iso(moment: datetime | None) -> str | None
```

*Call graph*: called by 6 (_conversation_row, _memory_rows, _resolve_chat, _usage_payload, agents_status, object_detail); 1 external calls (isoformat).


##### `_window_param`  (lines 2477–2493)

```
def _window_param(request: Request) -> int | None | Response
```

*Call graph*: called by 1 (workspace_usage); 1 external calls (Response).


##### `_usage_payload`  (lines 2496–2536)

```
def _usage_payload(report: MemberSpendReport | SpendReport) -> dict[str, object]
```

*Call graph*: calls 1 internal fn (_iso); called by 1 (workspace_usage).


##### `skills`  (lines 2539–2563)

```
async def skills(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 2 internal fn (agent_skills, _panel_gate); 1 external calls (JSONResponse).


##### `_community_refusal`  (lines 2569–2573)

```
def _community_refusal(fault: Exception) -> Response
```

*Call graph*: called by 2 (community_skill, community_skills); 1 external calls (Response).


##### `community_skills`  (lines 2580–2596)

```
async def community_skills(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 2 internal fn (_community_refusal, _panel_gate); 3 external calls (JSONResponse, Response, listing).


##### `community_skill`  (lines 2599–2620)

```
async def community_skill(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 2 internal fn (_community_refusal, _panel_gate); 3 external calls (JSONResponse, Response, fetch).


##### `workspace_memory`  (lines 2623–2700)

```
async def workspace_memory(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 5 internal fn (recent_memory, search_memory, decode, _audience_for, _memory_rows); 6 external calls (__init__, gather, audience_subjects, conversation_audience, JSONResponse, Response).


##### `_memory_rows`  (lines 2703–2713)

```
def _memory_rows(found: tuple[MemoryMatch, ...]) -> list[dict[str, object]]
```

*Call graph*: calls 1 internal fn (_iso); called by 1 (workspace_memory).


##### `connections`  (lines 2716–2725)

```
async def connections(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 2 internal fn (list_agent_connections, _panel_gate); 1 external calls (JSONResponse).


##### `connection_pool`  (lines 2728–2738)

```
async def connection_pool(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 2 internal fn (list_connections, _audience_for); 1 external calls (JSONResponse).


##### `github_coverage`  (lines 2741–2747)

```
async def github_coverage(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 2 internal fn (github_coverage, _audience_for); 1 external calls (JSONResponse).


##### `conversations`  (lines 2750–2781)

```
async def conversations(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 4 internal fn (list_agent_conversations, _conversation_row, _panel_gate, _searched); 1 external calls (JSONResponse).


##### `_searched`  (lines 2784–2788)

```
def _searched(request: Request) -> str | None
```

*Call graph*: called by 1 (conversations).


##### `_conversation_row`  (lines 2791–2825)

```
def _conversation_row(entry: ListedConversation, member_id: UUID, agent: dict[str, str] | None=None) -> dict[str, object]
```

*Call graph*: calls 2 internal fn (_commentable, _iso); called by 2 (_resolve_chat, conversations).


##### `_readable_conversation`  (lines 2828–2848)

```
async def _readable_conversation(ctx: SurfaceContext, request: Request, conversation_id: UUID | None=None) -> tuple[UUID, UUID, 'SlotViewer'] | Response
```

*Call graph*: calls 3 internal fn (readable_conversation, _opens, _panel_gate); called by 4 (_conversation_history, _slot_target, conversation_attachment, conversation_transcript); 3 external calls (__init__, Response, UUID).


##### `conversation_transcript`  (lines 2851–2869)

```
async def conversation_transcript(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 4 internal fn (_conversation_history, _conversation_messages, _history_cursor, _readable_conversation); 1 external calls (JSONResponse).


##### `_member_chat_page`  (lines 2872–2900)

```
async def _member_chat_page(ctx: SurfaceContext, request: Request) -> tuple[UUID, UUID, 'SlotViewer'] | Response
```

*Call graph*: calls 4 internal fn (_agent_param, _audience_for, _member_chat, _opens); called by 2 (_conversation_history, conversation_attachment); 4 external calls (__init__, Response, web_extension, UUID).


##### `_conversation_history`  (lines 2903–2924)

```
async def _conversation_history(ctx: SurfaceContext, request: Request, cursor: str) -> Response
```

*Call graph*: calls 3 internal fn (_history_messages, _member_chat_page, _readable_conversation); called by 1 (conversation_transcript); 2 external calls (JSONResponse, Response).


##### `_inbox_attachment`  (lines 2927–2938)

```
def _inbox_attachment(path: str) -> bool
```

*Call graph*: called by 1 (conversation_attachment).


##### `conversation_attachment`  (lines 2941–2981)

```
async def conversation_attachment(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 5 internal fn (list_workspace_files, read_workspace_file, _inbox_attachment, _member_chat_page, _readable_conversation); 5 external calls (__init__, Response, raster_image_media_type, validated_image_preview, log).


##### `_slot_target`  (lines 3001–3025)

```
async def _slot_target(ctx: SurfaceContext, request: Request) -> SlotTarget | Response
```

*Call graph*: calls 2 internal fn (conversation_subagent_turns, _readable_conversation); called by 2 (conversation_slot, conversation_slots); 3 external calls (__init__, Response, UUID).


##### `_slot_context`  (lines 3028–3044)

```
async def _slot_context(ctx: SurfaceContext, target: SlotTarget, ext: ExtensionContext) -> ConversationSlotContext | None
```

*Call graph*: calls 2 internal fn (conversation_audience, read_transcript); called by 2 (conversation_slot, conversation_slots); 2 external calls (__init__, replace).


##### `_project_slot_context`  (lines 3047–3131)

```
async def _project_slot_context(ctx: SurfaceContext, slot_context: ConversationSlotContext, extension: str, content: type[BaseModel], root_conversation_id: UUID | None, viewer: SlotViewer) -> Conversa
```

*Call graph*: calls 5 internal fn (artifact_link, artifact_preview_link, conversation_changes, list_conversation_artifacts, list_conversation_member_objects); called by 2 (conversation_slot, conversation_slots); 7 external calls (__init__, __init__, __init__, __init__, replace, raster_image_media_type, urlsplit).


##### `_authorized_slot_payload`  (lines 3134–3178)

```
def _authorized_slot_payload(payload: ConversationSlotPayload, context: ConversationSlotContext) -> ConversationSlotPayload
```

*Call graph*: called by 1 (conversation_slot); 1 external calls (model_copy).


##### `conversation_slots`  (lines 3181–3225)

```
async def conversation_slots(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 4 internal fn (summarize_conversation_slot, _project_slot_context, _slot_context, _slot_target); 4 external calls (replace, JSONResponse, Response, log).


##### `conversation_slot`  (lines 3228–3255)

```
async def conversation_slot(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 5 internal fn (read_conversation_slot, _authorized_slot_payload, _project_slot_context, _slot_context, _slot_target); 2 external calls (JSONResponse, Response).


##### `_changes_projection`  (lines 3258–3261)

```
def _changes_projection(ctx: ConversationSlotContext) -> WorkspaceChanges
```

*Call graph*: called by 2 (_read_changes, _summarize_changes).


##### `_read_changes`  (lines 3264–3265)

```
async def _read_changes(ctx: ConversationSlotContext) -> WorkspaceChanges
```

*Call graph*: calls 1 internal fn (_changes_projection).


##### `_summarize_changes`  (lines 3268–3269)

```
async def _summarize_changes(ctx: ConversationSlotContext) -> int | None
```

*Call graph*: calls 1 internal fn (_changes_projection).


##### `_artifacts_projection`  (lines 3282–3285)

```
def _artifacts_projection(ctx: ConversationSlotContext) -> ArtifactsSlotPayload
```

*Call graph*: called by 2 (_read_artifacts, _summarize_artifacts).


##### `_read_artifacts`  (lines 3288–3289)

```
async def _read_artifacts(ctx: ConversationSlotContext) -> ArtifactsSlotPayload
```

*Call graph*: calls 1 internal fn (_artifacts_projection).


##### `_summarize_artifacts`  (lines 3292–3294)

```
async def _summarize_artifacts(ctx: ConversationSlotContext) -> int | None
```

*Call graph*: calls 1 internal fn (_artifacts_projection).


##### `workspace_credentials`  (lines 3307–3315)

```
async def workspace_credentials(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 2 internal fn (list_credential_slots, _audience_for); 1 external calls (JSONResponse).


##### `workspace_team`  (lines 3318–3336)

```
async def workspace_team(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 2 internal fn (list_members, _audience_for); 1 external calls (JSONResponse).


##### `workspace_sources`  (lines 3339–3350)

```
async def workspace_sources(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 2 internal fn (list_sources, _audience_for); 1 external calls (JSONResponse).


##### `workspace_surfaces`  (lines 3353–3364)

```
async def workspace_surfaces(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 2 internal fn (list_installations, _audience_for); 1 external calls (JSONResponse).


##### `workspace_first_run`  (lines 3392–3426)

```
async def workspace_first_run(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 3 internal fn (github_coverage, list_installations, _audience_for); 2 external calls (__init__, JSONResponse).


##### `connector_catalog`  (lines 3429–3457)

```
async def connector_catalog(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 2 internal fn (connector_catalog, _audience_for); 3 external calls (__init__, JSONResponse, Response).


##### `_held_providers`  (lines 3508–3519)

```
async def _held_providers(ctx: SurfaceContext, member_id: UUID, *, admin: bool) -> frozenset[str]
```

*Call graph*: calls 3 internal fn (github_coverage, list_connections, list_installations); called by 1 (workspace_starters).


##### `fill_starters`  (lines 3522–3611)

```
def fill_starters(slate: Slate, held: frozenset[str], taken: frozenset[str], installed: tuple[StarterApp, ...]=()) -> tuple[tuple[StarterRow, ...], UnlockRow | None]
```

*Call graph*: called by 1 (workspace_starters); 4 external calls (__init__, __init__, __init__, get).


##### `_solvent`  (lines 3614–3623)

```
async def _solvent() -> bool
```

*Call graph*: called by 1 (workspace_starters); 2 external calls (read_headroom, web_extension).


##### `_recalled`  (lines 3626–3633)

```
async def _recalled(ctx: SurfaceContext, member_id: UUID) -> tuple[str, ...]
```

*Call graph*: calls 1 internal fn (recent_memory); called by 1 (workspace_starters); 2 external calls (audience_subjects, conversation_audience).


##### `workspace_starters`  (lines 3636–3694)

```
async def workspace_starters(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 7 internal fn (agent_setup, _audience_for, _held_providers, _recalled, _setup_ready, _solvent, fill_starters); 5 external calls (__init__, __init__, gather, JSONResponse, web_extension).


##### `workspace_usage`  (lines 3697–3772)

```
async def workspace_usage(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 5 internal fn (member_spend, spend_rollup, _audience_for, _usage_payload, _window_param); 1 external calls (JSONResponse).


##### `connect_handoff`  (lines 3778–3802)

```
async def connect_handoff(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 2 internal fn (connect_url, _member_turn); 2 external calls (callback_page, RedirectResponse).


##### `_member_turn`  (lines 3805–3858)

```
async def _member_turn(ctx: SurfaceContext, request: Request, *, named_turn: UUID | None=None, allow_commentable: bool=False) -> tuple[UUID, UUID, str] | Response
```

*Call graph*: calls 5 internal fn (turn_detail, turn_owner, _audience_for, _commentable, _member_chat); called by 3 (chat, connect_handoff, stream); 3 external calls (Response, web_extension, UUID).


##### `stream`  (lines 3861–3869)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 2 internal fn (_events, _member_turn); 1 external calls (StreamingResponse).


##### `_event`  (lines 3872–3873)

```
def _event(name: str, payload: dict[str, object]) -> bytes
```

*Call graph*: called by 1 (_events); 1 external calls (dumps).


##### `_pending_prompts`  (lines 3876–3889)

```
async def _pending_prompts(ctx: SurfaceContext, request_: CredentialRequest) -> dict[str, object] | None
```

*Call graph*: calls 1 internal fn (credential_prompt_pending); called by 2 (_events, _open_handoffs).


##### `_file_payload`  (lines 3892–3905)

```
def _file_payload(ctx: SurfaceContext, artifact: SharedArtifact) -> dict[str, object]
```

*Call graph*: calls 2 internal fn (artifact_link, artifact_preview_link); called by 2 (_events, _transcript_aids).


##### `_opens`  (lines 3908–3909)

```
def _opens(audience: WebAudience) -> frozenset[UUID]
```

*Call graph*: called by 4 (_events, _member_chat_page, _readable_conversation, transcript).


##### `_created_apps`  (lines 3912–3943)

```
async def _created_apps(ctx: SurfaceContext, created: Mapping[str, tuple[ObjectRef, ...]], opens: frozenset[UUID]) -> dict[str, list[dict[str, object]]]
```

*Call graph*: calls 1 internal fn (list_agents); called by 2 (_events, _transcript_aids).


##### `_events`  (lines 3946–3994)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, member_id: UUID, since: str, email: str) -> AsyncIterator[bytes]
```

*Call graph*: calls 13 internal fn (connect_available, conversation_subagent_turns, shared_artifacts, tail, turn_detail, _connect_control, _created_apps, _event, _file_payload, _opens (+3 more)); called by 1 (stream); 2 external calls (web_audience, web_extension).


##### `fulfill_credential`  (lines 3997–4025)

```
async def fulfill_credential(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 4 internal fn (fulfill_credential_request, _authenticate, _form, _framed_length); 2 external calls (JSONResponse, Response).


##### `admin_index`  (lines 4028–4080)

```
async def admin_index(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 3 internal fn (list_installations, spend_caps, _audience_for); 5 external calls (__init__, JSONResponse, Response, granted_emails, web_extension).


##### `_object_gate`  (lines 4083–4097)

```
async def _object_gate(ctx: SurfaceContext, request: Request) -> tuple[UUID, WebAudience, PortalKind] | Response
```

*Call graph*: calls 2 internal fn (object_kind, _audience_for); called by 2 (object_detail, object_index); 1 external calls (Response).


##### `_object_agent`  (lines 4100–4110)

```
def _object_agent(request: Request, audience: WebAudience) -> AgentSummary | Response
```

*Call graph*: called by 2 (object_detail, object_index); 2 external calls (Response, UUID).


##### `_kind_payload`  (lines 4113–4120)

```
def _kind_payload(kind: PortalKind) -> dict[str, object]
```

*Call graph*: calls 2 internal fn (applying_kinds, deleting_kinds); called by 2 (object_detail, object_index).


##### `_filter_value`  (lines 4123–4130)

```
def _filter_value(raw: str) -> JsonValue
```

*Call graph*: called by 1 (object_index); 1 external calls (loads).


##### `_fanout_token`  (lines 4133–4138)

```
def _fanout_token(walking: dict[str, str]) -> str | None
```

*Call graph*: called by 1 (object_index); 1 external calls (dumps).


##### `_fanout_walks`  (lines 4141–4158)

```
def _fanout_walks(token: str) -> dict[UUID, str] | None
```

*Call graph*: called by 1 (object_index); 2 external calls (loads, UUID).


##### `_merged_rank`  (lines 4161–4174)

```
def _merged_rank(row: dict[str, object], order_by: str) -> tuple[int, float | str, str]
```


##### `object_index`  (lines 4177–4261)

```
async def object_index(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 7 internal fn (list_member_objects, _fanout_token, _fanout_walks, _filter_value, _kind_payload, _object_agent, _object_gate); 4 external calls (__init__, replace, JSONResponse, Response).


##### `object_detail`  (lines 4264–4311)

```
async def object_detail(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 5 internal fn (member_object, _iso, _kind_payload, _object_agent, _object_gate); 2 external calls (JSONResponse, Response).


##### `_sse`  (lines 4314–4346)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `intents`  (lines 4349–4354)

```
async def intents(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 1 internal fn (_panel_gate); 1 external calls (submit_intent).


##### `_write_agent`  (lines 4361–4379)

```
def _write_agent(request: Request, audience: WebAudience, stated: object=None) -> AgentSummary | Response
```

*Call graph*: called by 1 (object_write); 2 external calls (Response, UUID).


##### `_direct_result`  (lines 4382–4386)

```
def _direct_result(frame: TerminalFrame, name: str) -> Response
```

*Call graph*: called by 1 (object_write); 1 external calls (JSONResponse).


##### `object_write`  (lines 4389–4463)

```
async def object_write(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 7 internal fn (admit, conversation_for, object_kind, tail, _audience_for, _direct_result, _write_agent); 7 external calls (__init__, timeout, dumps, conversation_audience, JSONResponse, json, Response).


##### `object_changes`  (lines 4469–4497)

```
async def object_changes(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 2 internal fn (recent_object_changes, _audience_for); 2 external calls (JSONResponse, Response).


##### `settings`  (lines 4500–4512)

```
async def settings(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 1 internal fn (_panel_gate); 1 external calls (agent_settings).


##### `agent_setup`  (lines 4515–4543)

```
async def agent_setup(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 5 internal fn (agent_setup, _has_own_page, _panel_gate, _provider_label, _provider_summary); 1 external calls (JSONResponse).


##### `_has_own_page`  (lines 4546–4571)

```
async def _has_own_page(ctx: SurfaceContext, agent_id: UUID, member_id: UUID) -> bool
```

*Call graph*: calls 1 internal fn (list_member_objects); called by 1 (agent_setup); 1 external calls (__init__).


##### `homepage`  (lines 4574–4592)

```
async def homepage(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 4 internal fn (_assets_published, _homepage_state, _panel_gate, apps); 1 external calls (JSONResponse).


##### `_homepage_state`  (lines 4595–4652)

```
async def _homepage_state(ctx: SurfaceContext, summary: AgentSummary, member_id: UUID, admin: bool) -> dict[str, JsonValue]
```

*Call graph*: calls 2 internal fn (list_member_objects, apps); called by 2 (agents_index, homepage); 4 external calls (__init__, shipped_app_slug, homepage_embed_url, shipped_homepage_url).


##### `preview`  (lines 4670–4701)

```
async def preview(ctx: SurfaceContext, request: Request) -> Response
```

*Call graph*: calls 4 internal fn (render_preview, _audience_for, _form, _framed_length); 2 external calls (PurePosixPath, Response).


### `extensions/app_chat/ufo_ext_app_chat/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it has an `__init__.py` file. That means other parts of the system can refer to this folder using normal Python import paths, such as importing modules from `ufo_ext_app_chat`.

There is no setup logic, configuration, or feature code here. Its value is structural: it tells Python and project tooling that the surrounding directory belongs together as one package. Without this file, some Python environments or older tooling might not recognize the folder as a package, which could make imports fail or make the extension harder to discover.

A simple analogy is a blank cover page in a binder. The page does not contain instructions, but it clearly marks where a section begins so the rest of the binder can be organized and referenced correctly.


### Memory inspection surface
The memory web surface gives authorized operators a read-only page and JSON API for inspecting workspace memory records.

### `extensions/memory/ufo_ext_memory/surface.py`

`io_transport` · `request handling`

This file is the doorway for a “memory explorer” page. Its job is to show an operator what the Memory extension has stored for a selected workspace, much like opening a read-only filing cabinet. Without this file, the stored memories might still exist, but there would be no simple operator web surface for viewing them.

The file defines two web actions. One serves a static HTML page from `static/memory.html`. That page is the browser app the operator sees. The other returns the actual memory records as JSON, which is a plain data format browsers and tools can read.

The important safety detail is that the page relies on the platform’s operator session and workspace binding. In plain terms, the request must already be tied to a verified operator and a specific workspace. When the JSON endpoint reads memory rows, it creates an extension-specific context so it can access the Memory extension’s own storage table, but the read is still scoped to the currently bound workspace. This keeps one workspace’s memory from leaking into another.

At the bottom, `ROUTES` connects web paths to these actions: load the page, bind an operator session, and fetch the memory list.

#### Function details

##### `app_page`  (lines 27–30)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function returns the Memory explorer web page to the operator’s browser. It is used when someone opens the surface’s main page.

**Data flow**: It receives the surface context and the incoming web request. It checks whether the static HTML file was successfully loaded when the module started. If the file is present, it wraps the HTML text in an HTTP response and sends it back; if the file is missing, it raises an error instead of serving a broken page.

**Call relations**: The route table calls this function for a GET request to the surface root path. Its only handoff is to `HTMLResponse`, which turns the already-loaded page text into a browser response.

*Call graph*: 1 external calls (HTMLResponse).


##### `memories`  (lines 33–42)

```
async def memories(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function returns every memory item for the currently bound workspace as JSON, newest first according to the store query it uses. It gives the browser page the data it needs to display the memory inventory.

**Data flow**: It receives the surface context, which includes the workspace id, and the incoming request. It builds an extension context for the Memory extension’s own scoped storage, with no declared credential access. Then it asks the memory store for the inventory for that workspace. The returned memory items are converted into JSON-friendly dictionaries and sent back in a JSON HTTP response.

**Call relations**: The route table calls this function for GET requests to `api/memories`. Inside, it creates `ScopedStore`, `CredentialAccess`, and `ExtensionContext` so it can read the Memory extension’s table, then hands the transaction and workspace id to `ufo_ext_memory.store.inventory`. Finally, it passes the serialized results to `JSONResponse` so the web client can consume them.

*Call graph*: 5 external calls (__init__, __init__, __init__, JSONResponse, inventory).


### Portal feature helpers
These helpers decide web agent access, expose community skill browsing, process setup/settings submissions, and build personalized start-screen suggestions.

### `extensions/web/ufo_ext_web/audience.py`

`domain_logic` · `request handling`

The web portal needs a clear answer to a simple question: “Is this person allowed to reach this agent here?” This file is that authority for the web surface. It treats a member’s email address as their web identity, and it stores explicit access grants as rows keyed by agent and email. Without this file, the portal would not have one consistent place to decide who can see private agents, who can chat, or how admins safely open private transcripts.

The main idea is layered access. Workspace admins can reach every agent. Non-admin members can reach agents visible to the whole workspace, agents explicitly granted to their email, and agents they own. There is also a special case for private extension conversations: a member may be allowed to chat with an agent through one of those conversations even if the agent is not generally listed for them.

The file also exposes three tools used from an agent conversation. Admins can grant or revoke a member’s web access to the current agent by email. Before doing so, the code checks that the speaker is a real workspace member, is an admin, and named an email that belongs to an existing member. A separate tool records that an admin acknowledged opening someone else’s private transcript. That record is important because the portal later uses it as proof that the private transcript may be shown.

#### Function details

##### `web_extension`  (lines 28–35)

```
def web_extension() -> ExtensionContext
```

**Purpose**: Builds the web extension’s own context so code running from the web surface can read and write the web extension’s private store. This is like getting the right filing-cabinet key before looking up audience grants.

**Data flow**: It takes no inputs. It creates a scoped store for the web extension and an empty credential access object, then wraps them in an ExtensionContext. The result is a ready-to-use extension context pointed at the web audience data.

**Call relations**: Surface code can call this when it has a SurfaceContext but needs to consult the web extension’s own stored rows. Inside, it constructs the store, credential access, and extension context that later audience checks or tool calls can use.

*Call graph*: 3 external calls (__init__, __init__, __init__).


##### `_grant_key`  (lines 38–39)

```
def _grant_key(agent_id: UUID, email: str) -> str
```

**Purpose**: Creates the exact storage key for one access grant: one agent and one member email. It keeps email matching consistent by trimming spaces and lowercasing the address.

**Data flow**: It receives an agent ID and an email address. It normalizes the email, combines it with the audience prefix and agent ID, and returns a single string key used in the store.

**Call relations**: _grant uses this key before writing a grant, and _revoke uses the same key before deleting one. Because both use the same helper, granting and revoking point to the same stored row.

*Call graph*: called by 2 (_grant, _revoke).


##### `granted_emails`  (lines 42–49)

```
async def granted_emails(store: ScopedStore) -> dict[UUID, tuple[str, ...]]
```

**Purpose**: Reads all saved web access grants and turns them into an admin-friendly map from agent to granted emails. This is useful for showing who has been granted access to which agents.

**Data flow**: It receives a scoped store. It lists every stored row under the audience prefix, splits each key into an agent ID and email, groups emails by agent, sorts each email list, and returns a dictionary of agent IDs to email tuples.

**Call relations**: This function reads the same stored grant rows that _grant writes and _revoke deletes. It depends on the store’s list operation to scan the audience area and converts the saved text agent IDs back into UUID objects.

*Call graph*: calls 1 internal fn (list); 1 external calls (UUID).


##### `_granted_agent_ids`  (lines 52–59)

```
async def _granted_agent_ids(store: ScopedStore, email: str) -> frozenset[UUID]
```

**Purpose**: Finds all agents that have been explicitly granted to one email address. It answers the question, “Which private agents did admins open for this member?”

**Data flow**: It receives a store and an email. It normalizes the email, scans all audience grant rows, keeps only rows whose saved email matches, converts their agent IDs into UUID objects, and returns them as a frozen set.

**Call relations**: web_audience calls this while building a non-admin member’s view of the portal. It supplies the explicit grant part of the access decision, alongside workspace-visible agents and agents owned by the member.

*Call graph*: calls 1 internal fn (list); called by 1 (web_audience); 1 external calls (UUID).


##### `WebAudience.allows`  (lines 73–74)

```
def allows(self, agent_id: UUID) -> bool
```

**Purpose**: Checks whether this member’s normal web audience includes a particular agent. It is used when the portal needs a yes-or-no answer before showing or opening an agent.

**Data flow**: It receives an agent ID. It looks through the WebAudience object’s main agent list and returns true if any listed agent has that ID, otherwise false. It does not change anything.

**Call relations**: The web surface’s chat-resolution code calls this when deciding whether a requested agent is inside the member’s allowed portal audience. It relies on web_audience having already built the correct list.

*Call graph*: called by 1 (_resolve_chat).


##### `WebAudience.allows_chat`  (lines 76–77)

```
def allows_chat(self, agent_id: UUID) -> bool
```

**Purpose**: Checks whether this member may chat with a particular agent, including special private conversation access. This can be broader than the normal visible agent list.

**Data flow**: It receives an agent ID. It looks through chat_agents, which combines the normal allowed agents with conversation-only agents, and returns true if the ID appears there.

**Call relations**: It builds on the chat_agents property. Code that needs a chat-specific permission check can use this instead of allows, because private extension conversations may permit chat even when the agent is not generally visible.


##### `WebAudience.chat_agents`  (lines 80–81)

```
def chat_agents(self) -> tuple[AgentSummary, ...]
```

**Purpose**: Returns every agent this member can chat with in the portal. It combines ordinary visible or granted agents with agents reachable only through member-private extension conversations.

**Data flow**: It reads the WebAudience object’s agents tuple and conversation_agents tuple. It returns a new tuple containing both groups, without changing the object.

**Call relations**: WebAudience.allows_chat uses this property to make a chat permission decision. The split between agents and conversation_agents is created by web_audience, and this property joins them for chat-time checks.


##### `web_audience`  (lines 84–116)

```
async def web_audience(surface: SurfaceContext, extension: ExtensionContext, email: str) -> WebAudience
```

**Purpose**: Builds the complete web-portal view for one member email: whether they are an admin, which agents they can normally see, and which extra agents they can chat with through private conversations.

**Data flow**: It receives a surface context, an extension context, and an email. It normalizes the email, reads the workspace seat snapshot inside a transaction, checks whether the email belongs to an admin and to which member, lists all agents, then filters those agents according to the access rules. It returns a WebAudience object.

**Call relations**: This is the central audience-building function. It calls the seat system to learn workspace membership, asks the surface for the available agents and member-private extension agent IDs, and uses _granted_agent_ids to add explicit grants. Portal routes can then use the returned WebAudience to answer access questions.

*Call graph*: calls 4 internal fn (transaction, list_agents, member_extension_agent_ids, _granted_agent_ids); 2 external calls (__init__, __init__).


##### `_refusal`  (lines 123–124)

```
def _refusal(text: str) -> ToolResult
```

**Purpose**: Creates a standard error result for a tool when the requested access change or transcript action is not allowed. It keeps refusal replies consistent and clearly marked as errors.

**Data flow**: It receives a plain text message. It wraps that message in TextContent, puts it in a ToolResult, marks the result as an error, and returns it.

**Call relations**: _gate uses this for failed grant or revoke pre-checks, and _read_private_transcript uses it when transcript access cannot be recorded. It hands back the final tool response instead of letting the operation continue.

*Call graph*: called by 2 (_gate, _read_private_transcript); 2 external calls (__init__, __init__).


##### `_gate`  (lines 127–145)

```
async def _gate(ctx: ToolContext, extension: ExtensionContext, args: WebAccessInput) -> ToolResult | None
```

**Purpose**: Performs the shared safety checks before an admin can grant or revoke web access. It makes sure the speaker is a workspace member, is an admin, supplied an email, and named an existing workspace member.

**Data flow**: It receives the current tool context, the extension context, and the email input. It checks speaker identity, checks admin status, validates that the email is not blank, reads the workspace member snapshot in a transaction, and confirms the email exists. It returns a refusal ToolResult if something is wrong, or None if the operation may continue.

**Call relations**: _grant and _revoke both call this before touching the access store. When a check fails, _gate uses _refusal to produce the tool response; when checks pass, it hands control back so the caller can write or delete the grant.

*Call graph*: calls 3 internal fn (transaction, speaker_is_admin, _refusal); called by 2 (_grant, _revoke); 1 external calls (__init__).


##### `_grant`  (lines 148–170)

```
async def _grant(ctx: ToolContext, args: WebAccessInput) -> ToolResult
```

**Purpose**: Implements the grant_web_access tool. It lets an admin give a workspace member web-portal access to the current agent, unless the current agent is the main agent, which already answers everyone.

**Data flow**: It receives the tool context and an input email. It confirms an extension context is present, runs _gate, skips storage if the current agent is the main agent, otherwise writes a grant row keyed by current agent and normalized email, including who granted it. It returns a ToolResult explaining what happened.

**Call relations**: This function is registered as the handler for the grant_web_access tool. It depends on _gate for permission checks, _grant_key for the storage key, and the tool context for the current agent, speaker, and main-agent check.

*Call graph*: calls 3 internal fn (agent_is_main, _gate, _grant_key); 2 external calls (__init__, __init__).


##### `_revoke`  (lines 173–195)

```
async def _revoke(ctx: ToolContext, args: WebAccessInput) -> ToolResult
```

**Purpose**: Implements the revoke_web_access tool. It lets an admin remove a member’s explicit web access to the current agent, while making clear that main-agent access cannot really be removed.

**Data flow**: It receives the tool context and an input email. It confirms an extension context is present, runs _gate, deletes the matching grant row from the store, then checks whether the current agent is the main agent. It returns a ToolResult saying either that the member still reaches the main agent or that access to this agent was removed.

**Call relations**: This function is registered as the handler for the revoke_web_access tool. Like _grant, it uses _gate before changing data and _grant_key to target the exact grant row, so it deletes the same kind of row that granting creates.

*Call graph*: calls 3 internal fn (agent_is_main, _gate, _grant_key); 2 external calls (__init__, __init__).


##### `_read_private_transcript`  (lines 205–232)

```
async def _read_private_transcript(ctx: ToolContext, args: PrivateTranscriptInput) -> ToolResult
```

**Purpose**: Implements the read_private_transcript tool. It records an admin’s acknowledgement before the web portal shows another member’s private conversation transcript.

**Data flow**: It receives the tool context and a conversation ID. It checks that the speaker is a member and an admin, then asks record_transcript_access to record access for this workspace, conversation, agent, and admin member. If there is nothing eligible to record, it returns an error; otherwise it returns a message confirming whose private conversation was opened and that the access was logged.

**Call relations**: This function is registered as the handler for the read_private_transcript tool. It uses _refusal for denied or inapplicable cases and delegates the actual audit record creation to record_transcript_access, which the portal later relies on when deciding whether to show the transcript.

*Call graph*: calls 2 internal fn (speaker_is_admin, _refusal); 3 external calls (__init__, __init__, record_transcript_access).


### `extensions/web/ufo_ext_web/community.py`

`domain_logic` · `request handling`

The Community tab needs two things from the public skills directory: a short list of skills to show, and the full SKILL.md document when someone opens one for install review. This file is the bridge to that outside service. With no search text, it reads the skills.sh homepage payload and extracts the leaderboard. With search text, it calls the public search API. Either way, it returns simple skill records with a name, source repository, and install count.

The file is careful because the outside directory is rate limited and may change shape. It stores listing results for a short time, like keeping a recently used menu on the counter instead of asking the restaurant again. It stores fetched skill documents for the life of the process, so reopening the same skill does not spend another request. It also limits how much data it will read, so a broken or unexpectedly huge response cannot consume too much memory.

When fetching a single skill, it downloads the directory’s JSON package, looks for SKILL.md, and reads its YAML front matter. YAML front matter is the small metadata block at the top of a Markdown file. If the document is missing, malformed, or lacks a name or description, the file returns no document rather than inventing data.

#### Function details

##### `_refusal`  (lines 43–49)

```
def _refusal(code: int) -> CommunityUnavailable
```

**Purpose**: This helper turns an HTTP failure code from skills.sh into a user-readable error. It gives a special, clearer message when the directory says the rate limit has been reached.

**Data flow**: It receives a numeric response code. If the code means “too many requests,” it creates an error explaining the 60-reads-an-hour limit; otherwise it creates an error saying which code the directory returned. The result is an exception object that callers raise.

**Call relations**: The low-level network readers call this when skills.sh does not answer successfully. `_search` uses it for failed search API calls, and `_body` uses it for failed streamed downloads, so both paths report failures in the same friendly way.

*Call graph*: called by 2 (_body, _search); 1 external calls (__init__).


##### `CommunitySkills.listing`  (lines 79–89)

```
async def listing(self, query: str) -> list[CommunitySkill]
```

**Purpose**: This is the main entry point for showing the Community skill list. It returns either popular skills or search results, while avoiding repeated calls to skills.sh when a fresh cached answer already exists.

**Data flow**: It receives the user’s search text. It first checks the listing cache for that exact text and returns the saved list if it is still fresh. If not, it opens an HTTP client, asks either the leaderboard reader or the search reader for results, trims the list to the display limit, stores it with the current time, and returns it.

**Call relations**: The web route for the Community narrowing would call this when it needs rows for the Skills tab. Inside, it chooses `_popular` when the query is empty and `_search` when the user typed something, using `_client` to create the temporary network client.

*Call graph*: calls 3 internal fn (_client, _popular, _search); 1 external calls (monotonic).


##### `CommunitySkills.fetch`  (lines 91–116)

```
async def fetch(self, source: str, name: str) -> CommunityDocument | None
```

**Purpose**: This is the main entry point for reading one specific community skill’s document. It downloads and parses SKILL.md so the install screen can show the skill’s description and instructions.

**Data flow**: It receives a repository source such as `owner/repo` and a skill name. It checks the document cache first. If missing, it builds the download URL, reads the response body, decodes the JSON, searches the returned files for `SKILL.md`, parses that file, stores the result in the cache, and returns either a `CommunityDocument` or `None` if no readable document was found.

**Call relations**: The install review flow calls this after a user chooses a skill from the listing. It relies on `_client` for the HTTP client, `_body` for safe downloading, and `_parse` for understanding the Markdown document’s metadata.

*Call graph*: calls 3 internal fn (_body, _client, _parse); 2 external calls (__init__, loads).


##### `CommunitySkills._client`  (lines 118–119)

```
def _client(self, timeout: float) -> httpx.AsyncClient
```

**Purpose**: This creates the asynchronous HTTP client used to talk to skills.sh. It centralizes timeout, redirect, and test-transport settings so all network reads behave consistently.

**Data flow**: It receives a timeout value. It combines that timeout with the optional injected transport and redirect-following setting, then returns a ready-to-use `httpx.AsyncClient`, which is an HTTP client designed for async code.

**Call relations**: `listing` and `fetch` call this just before they contact the directory. Tests can provide a fake transport here, letting the code be tested without real internet access.

*Call graph*: called by 2 (fetch, listing); 1 external calls (AsyncClient).


##### `CommunitySkills._popular`  (lines 121–136)

```
async def _popular(self, client: httpx.AsyncClient) -> list[CommunitySkill]
```

**Purpose**: This reads the skills.sh leaderboard when the user has not searched for anything. It extracts skill entries from the site’s page payload and sorts them by install count.

**Data flow**: It receives an HTTP client. It downloads the leaderboard page with a special header, scans the text for embedded JSON-looking skill entries, converts each valid entry into a `CommunitySkill`, removes duplicates by source and name, and returns the skills from most installed to least installed. If no valid entries are found, it raises a clear unavailable error.

**Call relations**: `listing` calls this for the default Community view. It uses `_body` to safely download the page and `_entry` to validate each possible skill row before returning the ranked list.

*Call graph*: calls 2 internal fn (_body, _entry); called by 1 (listing); 2 external calls (__init__, loads).


##### `CommunitySkills._search`  (lines 138–147)

```
async def _search(self, client: httpx.AsyncClient, query: str) -> list[CommunitySkill]
```

**Purpose**: This calls the public skills.sh search API when the user types a search query. It turns the API’s response into the same simple skill rows used by the rest of the UI.

**Data flow**: It receives an HTTP client and the search text. It sends a GET request with the query and result limit, checks for a successful response, reads the returned JSON, converts each listed skill through `_entry`, drops invalid rows, sorts the rest by install count, and returns the list.

**Call relations**: `listing` calls this whenever the query is not empty. If the API refuses the request, it hands the status code to `_refusal`; for usable responses, it depends on `_entry` to keep only well-formed skills.

*Call graph*: calls 2 internal fn (_entry, _refusal); called by 1 (listing); 1 external calls (get).


##### `CommunitySkills._entry`  (lines 149–156)

```
def _entry(self, entry: object) -> CommunitySkill | None
```

**Purpose**: This validates and normalizes one raw skill entry from skills.sh. It protects the portal from odd or incomplete data by only accepting entries with a name and a safe-looking repository source.

**Data flow**: It receives one unknown object from a page payload or API response. If the object is not a dictionary, or if the name or repository source is missing or badly shaped, it returns `None`. Otherwise it creates and returns a `CommunitySkill` with name, source, and install count.

**Call relations**: Both `_popular` and `_search` pass their raw directory entries through this function. That makes the two listing paths produce the same clean data shape before `listing` caches and returns the results.

*Call graph*: called by 2 (_popular, _search); 1 external calls (__init__).


##### `CommunitySkills._body`  (lines 158–178)

```
async def _body(self, client: httpx.AsyncClient, url: str, cap: int, headers: dict[str, str] | None=None) -> bytes
```

**Purpose**: This safely downloads a response body from skills.sh. It reads the response in pieces and stops if the service returns an error or sends more data than this code is willing to accept.

**Data flow**: It receives an HTTP client, a URL, a maximum byte limit, and optional headers. It opens a streaming GET request, checks the status code, adds each incoming byte chunk to a list, tracks the total size, and raises an error if the response is too large. On success, it returns all chunks joined into one byte string.

**Call relations**: `_popular` uses this to read the leaderboard page, and `fetch` uses it to download a skill package. When the status code is not successful, it delegates the message-building to `_refusal`; when the body is too large, it raises its own clear unavailable error.

*Call graph*: calls 1 internal fn (_refusal); called by 2 (_popular, fetch); 2 external calls (__init__, stream).


##### `CommunitySkills._parse`  (lines 180–199)

```
def _parse(self, document: str) -> CommunityDocument | None
```

**Purpose**: This reads a downloaded `SKILL.md` file and extracts the parts the install screen needs. It only accepts documents with valid front matter containing both a name and a description.

**Data flow**: It receives the full Markdown document as text. It looks for a YAML metadata block at the top, parses that block safely, checks for required name and description fields, and separates the remaining Markdown as instructions. It returns a `CommunityDocument` when everything is usable, or `None` when the document does not meet those expectations.

**Call relations**: `fetch` calls this after it finds `SKILL.md` in the downloaded files. The parsed result is then cached and returned to the install review flow, so the UI can show the skill’s description and instructions without another directory read.

*Call graph*: called by 1 (fetch); 2 external calls (__init__, safe_load).


### `extensions/web/ufo_ext_web/panels.py`

`orchestration` · `request handling`

The web portal lets people do things like save an agent setting, connect Slack, request a credential prompt, rebuild reports, or add a team member. This file is the contract for those actions. It does not create separate special-purpose write APIs. Instead, every portal action is packaged as a prepared intent and sent through the same conversation system that chat uses. That matters because the conversation becomes the audit trail: who asked, what they asked, and what happened all live together.

The file first defines many small request shapes using Pydantic models, which are data checkers that reject malformed input before work starts. For example, a credential can only be deleted here, not filled in, because secrets must go through a private credential prompt. A connection can be connected or disconnected, not edited like a normal object.

The central path is `submit_intent`. It reads the web request, validates it, fills in missing safe defaults for partial agent updates, turns the intent into a `ToolIntent`, admits it into a durable “Portal actions” conversation, and waits for the final result. Helper functions translate that final result into JSON the browser can understand, including special cases for install links and billing portal links. The file also supplies first-run provider tiles and “unlock” suggestions, which explain what useful apps become possible after connecting certain tools.

#### Function details

##### `ApplyIntent.kinds`  (lines 78–82)

```
def kinds(cls) -> frozenset[str]
```

**Purpose**: Returns the full set of object kinds that a panel form is allowed to submit changes for. This keeps the user interface and the server-side rules tied to the same list.

**Data flow**: It reads the declared `kind` choices from the `ApplyIntent` model → extracts those fixed choices → returns them as a frozen set of strings.

**Call relations**: Other code can ask this model what object kinds are valid instead of keeping a separate copy. `ApplyIntent.applying_kinds` and `ApplyIntent.deleting_kinds` build on this same source of truth.

*Call graph*: 1 external calls (get_args).


##### `ApplyIntent.applying_kinds`  (lines 85–87)

```
def applying_kinds(cls) -> frozenset[str]
```

**Purpose**: Returns the object kinds that can be created or updated through an `apply` action. It excludes kinds that are only deleted or connected.

**Data flow**: It starts with all allowed kinds from `ApplyIntent.kinds` → removes credential-like delete-only kinds and connection-only kinds → returns the remaining set.

**Call relations**: The web surface calls this when deciding which object pages should show create or edit controls. Because it derives from the same validation rules, the page should not offer an action the server later refuses.

*Call graph*: called by 1 (_kind_payload).


##### `ApplyIntent.deleting_kinds`  (lines 90–96)

```
def deleting_kinds(cls) -> frozenset[str]
```

**Purpose**: Returns the object kinds that can be deleted or disconnected from the panel lane. In this file, every named kind can be the target of a delete-style action.

**Data flow**: It reads the allowed kinds from `ApplyIntent.kinds` → returns that same set as the delete-capable set.

**Call relations**: The web surface calls this when deciding where to show delete controls. It pairs with the validator on `ApplyIntent`, so the displayed options and accepted requests stay aligned.

*Call graph*: called by 1 (_kind_payload).


##### `ApplyIntent._verb_pairs_with_its_kind`  (lines 99–120)

```
def _verb_pairs_with_its_kind(self) -> 'ApplyIntent'
```

**Purpose**: Checks that an object action makes sense for the object kind. For example, credentials cannot be edited through a normal form, and connections can only be connected or disconnected.

**Data flow**: It receives a parsed `ApplyIntent` → inspects its verb, kind, spec, and create-only flag → either returns the same intent as valid or raises a validation error explaining the invalid pairing.

**Call relations**: Pydantic runs this automatically when a panel submission is validated. It protects `submit_intent` from turning an unsafe or impossible panel request into a real tool call.


##### `Unlock._names_offered_tiles_and_a_drawn_mark`  (lines 400–409)

```
def _names_offered_tiles_and_a_drawn_mark(self) -> 'Unlock'
```

**Purpose**: Checks that an unlock suggestion can actually be shown in the portal. It verifies that the icon is known and that every required provider has a first-run tile with a label and glyph.

**Data flow**: It receives an `Unlock` row → checks its icon against the known icon set and each provider name against the offered provider catalog → returns the row or raises a validation error.

**Call relations**: Pydantic runs this when unlock rows are created. This catches broken setup suggestions early, before the start screen could show a blank or confusing item.


##### `Unlock.missing`  (lines 411–415)

```
def missing(self, held: frozenset[str]) -> tuple[str, ...]
```

**Purpose**: Figures out which provider accounts a member still needs before a suggested app or workflow can run. It treats each requirement group as “any one of these is enough.”

**Data flow**: It receives the set of provider names the member already has → walks each requirement group → for unmet groups, chooses the first preferred provider name → returns the missing names in catalog order.

**Call relations**: Start or setup views can use this to explain what a member can build now and what is one or two connections away. It depends on the requirement groups already validated by `Unlock._names_offered_tiles_and_a_drawn_mark`.


##### `_tools_recorded`  (lines 584–595)

```
def _tools_recorded(labels: tuple[str, ...], budget: int) -> str
```

**Purpose**: Builds a short memory sentence describing the tools a team already uses during first-run setup. It trims the list gracefully so it fits within the memory provider’s size limit.

**Data flow**: It receives provider labels and a character budget → tries to write “My team uses ...” with as many labels as fit → if needed, replaces the rest with a count → returns the final sentence.

**Call relations**: `_tool_intent` calls this when turning a `ToolingIntent` into a memory update. It prevents first-run selections from producing a memory body that is too long.

*Call graph*: called by 1 (_tool_intent).


##### `ToolingIntent._picks_are_offered`  (lines 610–614)

```
def _picks_are_offered(self) -> 'ToolingIntent'
```

**Purpose**: Checks that every provider selected during first-run setup is one the portal actually offered. This prevents unknown provider names from becoming misleading memory text.

**Data flow**: It receives a parsed `ToolingIntent` → compares the submitted provider names with the first-run provider catalog → returns the intent or raises a validation error for the first unknown name.

**Call relations**: Pydantic runs this during panel intent validation. If it passes, `_tool_intent` can safely turn provider names into human-readable labels.


##### `_tool_intent`  (lines 682–836)

```
def _tool_intent(submitted: ApplyIntent | AddMemberIntent | AudienceIntent | ConnectGitHubIntent | ConnectImessageIntent | ConnectSlackIntent | CorrectionIntent | CredentialIntent | DigestRebuildInten
```

**Purpose**: Converts a validated panel submission into the exact tool call that the agent system should run. It is the translation layer between browser-friendly form data and the internal tool-call format.

**Data flow**: It receives a specific intent object, an optional credential slot description, and the memory body limit → matches the intent type → builds a `ToolIntent` with the correct tool name and input payload → returns that tool call. For object apply actions, it writes the object envelope as YAML so the existing object-apply tool can read it.

**Call relations**: `submit_intent` calls this after validation and deployment checks. It may call `_tools_recorded` for first-run tooling memory, and it hands the resulting `ToolIntent` back to `submit_intent`, which admits it into the conversation lane.

*Call graph*: calls 1 internal fn (_tools_recorded); called by 1 (submit_intent); 2 external calls (__init__, safe_dump).


##### `_outcome`  (lines 839–855)

```
def _outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Turns a finished tool run into the standard JSON response used by most panel actions. It reports success, failure, a user-facing message, and the turn identifier.

**Data flow**: It receives a terminal frame and the turn ID → checks whether the tool finished successfully → includes credential request data if present, otherwise returns “Saved.” on success → on failure, cleans up the error text and returns an unsuccessful response.

**Call relations**: `submit_intent` uses this as the normal result formatter. The specialized outcome helpers also call it when their tool did not finish successfully, so failures are reported consistently.

*Call graph*: called by 5 (_connect_outcome, _imessage_outcome, _portal_outcome, _rebuild_outcome, submit_intent); 1 external calls (JSONResponse).


##### `_connect_outcome`  (lines 858–882)

```
def _connect_outcome(submitted: ConnectSlackIntent | ConnectGitHubIntent, frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Extracts an install link from the final answer of the Slack or GitHub connection tools. These tools return links in slightly different shapes, so this function normalizes them for the browser.

**Data flow**: It receives the original connect intent, the terminal frame, and the turn ID → if the run failed, delegates to `_outcome` → for Slack, reads a JSON object from the tool text and uses its authorization URL or hint → for GitHub, searches the text for an install URL → returns JSON with the link, message, and turn ID.

**Call relations**: `submit_intent` calls this when the submitted intent was `ConnectSlackIntent` or `ConnectGitHubIntent`. It falls back to `_outcome` for non-success cases and otherwise prepares the URL the setup screen should open.

*Call graph*: calls 1 internal fn (_outcome); called by 1 (submit_intent); 2 external calls (loads, JSONResponse).


##### `_imessage_outcome`  (lines 885–906)

```
def _imessage_outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Reads the iMessage connection state from the tool’s final answer and turns it into a browser response. It tells the user what to do next and may include an opt-in link.

**Data flow**: It receives the terminal frame and turn ID → if the run failed, delegates to `_outcome` → extracts a JSON object from the frame text → validates the connection state, instruction, and optional link → returns whether the action is considered applied, the instruction message, the link, and the turn ID.

**Call relations**: `submit_intent` calls this after an iMessage connect intent reaches its terminal frame. It uses `_outcome` only for failed runs, while successful runs need this special JSON parsing.

*Call graph*: calls 1 internal fn (_outcome); called by 1 (submit_intent); 2 external calls (loads, JSONResponse).


##### `_portal_outcome`  (lines 909–923)

```
def _portal_outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Extracts the billing provider portal URL from the billing tool’s final answer. This lets an admin save a payment card through an external billing page.

**Data flow**: It receives the terminal frame and turn ID → if the run failed, delegates to `_outcome` → reads a JSON object from the tool text → requires a string portal URL → returns a successful JSON response containing that URL.

**Call relations**: `submit_intent` calls this for `PaymentMethodIntent`. It relies on the billing tool to decide whether the user is allowed to open billing, and uses `_outcome` for refusals.

*Call graph*: calls 1 internal fn (_outcome); called by 1 (submit_intent); 2 external calls (loads, JSONResponse).


##### `_rebuild_outcome`  (lines 926–933)

```
def _rebuild_outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Returns the rebuild tool’s own message to the browser. Rebuild actions usually queue work rather than immediately changing visible text, so the tool’s explanation is important.

**Data flow**: It receives the terminal frame and turn ID → if the run failed, delegates to `_outcome` → otherwise returns success with the frame text as the message.

**Call relations**: `submit_intent` calls this for report digest and page fact rebuild intents. It keeps the specific “what was queued” wording from the tool instead of replacing it with a generic saved message.

*Call graph*: calls 1 internal fn (_outcome); called by 1 (submit_intent); 1 external calls (JSONResponse).


##### `submit_intent`  (lines 964–1096)

```
async def submit_intent(ctx: SurfaceContext, request: Request, agent_id: UUID, member_id: UUID, email: str) -> Response
```

**Purpose**: Accepts one panel form submission, turns it into a safe tool call, runs it through the portal’s durable intent conversation, and waits for the final answer. This is the main write path for portal actions.

**Data flow**: It receives the surface context, HTTP request, selected agent ID, member ID, and member email → reads and size-checks the request body → validates it as one of the known panel intents → performs extra checks such as valid agent model, existing credential slot, and memory availability → converts it with `_tool_intent` → opens or reuses the member’s portal intent conversation → admits the tool call as a turn → tails the turn until it finishes, parks, or times out → returns JSON describing the result.

**Call relations**: This function is the hub of the file. It calls context methods to read agent details, find credential slots, create the conversation, retitle it, admit the turn, and watch the turn stream. When a terminal frame arrives, it chooses `_connect_outcome`, `_imessage_outcome`, `_portal_outcome`, `_rebuild_outcome`, or `_outcome` depending on what was submitted.

*Call graph*: calls 12 internal fn (admit, agent_detail, conversation_for, list_credential_slots, retitle_conversation, tail, _connect_outcome, _imessage_outcome, _outcome, _portal_outcome (+2 more)); 5 external calls (timeout, loads, conversation_audience, JSONResponse, body).


##### `_update_schema`  (lines 1099–1111)

```
def _update_schema(sandbox_sizes: tuple[str, ...]) -> dict[str, JsonValue]
```

**Purpose**: Builds the schema that tells the settings page which agent settings can be edited with generated form controls. It removes fields the page draws in custom ways, like prompt and icon.

**Data flow**: It receives the deploy’s available sandbox sizes → starts from the full `AgentSpec` JSON schema → removes custom-rendered fields and, when sandbox sizes are not offered, removes sandbox size too → returns the reduced schema.

**Call relations**: `agent_settings` calls this while preparing the settings projection. The returned schema lets the front end stay close to the real agent specification instead of maintaining a separate form description.

*Call graph*: called by 1 (agent_settings); 1 external calls (model_json_schema).


##### `agent_settings`  (lines 1114–1160)

```
async def agent_settings(ctx: SurfaceContext, agent_id: UUID, member_id: UUID, *, admin: bool, archivable: bool) -> Response
```

**Purpose**: Returns the data needed to draw an agent’s settings page. It includes the agent’s current configuration, deploy capabilities, available models, editable schema, and admin-only web audience information.

**Data flow**: It receives the surface context, agent ID, member ID, and flags saying whether the viewer is an admin and whether the agent can be archived → looks up the agent detail → if missing, returns a 404 response → for admins, reads granted web audience emails → builds a JSON response with agent metadata, deployment limits, model choices, current spec values, the update schema, and optional audience list.

**Call relations**: This is the read-side partner to `submit_intent`. It calls the surface context for agent detail, uses `_update_schema` to describe editable fields, and calls the web audience helpers when admin-only grant data is needed.

*Call graph*: calls 2 internal fn (agent_detail, _update_schema); 5 external calls (__init__, JSONResponse, Response, granted_emails, web_extension).


### `extensions/web/ufo_ext_web/starters.py`

`domain_logic` · `request handling for the start screen`

The start screen needs helpful first prompts, not generic examples. This file creates a small “slate” of ranked starter rows for one member: each row has a short title, a one-line explanation, and the sentence the member would say if they clicked it. It also allows one optional “check-in” row, but only when the member’s own unfinished work clearly deserves it.

The file is careful about cost and reliability. A slate is made only when somebody actually opens the screen. Once made, it is cached for 30 minutes. If the cached slate is still fresh, it is returned immediately. If it is stale, the old slate can still be shown while a new one is made, so the user is not left with an empty screen.

To avoid duplicate work, the file uses a short “claim” record in shared storage. This is like putting a sticky note on a task saying “I’m doing this,” so two browser tabs do not ask the model for the same slate at the same time. If model generation fails, it records a cooldown period so the next read does not immediately hit the same failure again. Model replies are validated and cleaned: invalid rows, unknown catalog entries, and duplicates are dropped without ruining the rest of the slate.

#### Function details

##### `Slate.fresh`  (lines 112–113)

```
def fresh(self, now: datetime) -> bool
```

**Purpose**: Checks whether a stored slate is still safe to reuse. It considers both age and whether the ranking instructions have changed since the slate was made.

**Data flow**: It receives the current time and reads the slate’s stored generation time and prompt digest. If the slate is younger than the allowed cache lifetime and was made with the current instructions, it returns true; otherwise it returns false.

**Call relations**: When the start screen asks `StarterCache.read` for suggestions, the cached slate is checked with `Slate.fresh`. A fresh slate ends the flow early, avoiding a new model call.


##### `starters_key`  (lines 124–125)

```
def starters_key(member_id: UUID) -> str
```

**Purpose**: Builds the storage key where one member’s cached starter slate is saved. This keeps each member’s suggestions separate.

**Data flow**: It takes a member ID, turns it into the project’s standard member subject string, and prefixes it with the starters cache label. The result is a single text key used for reading and writing storage.

**Call relations**: `StarterCache._held` uses this key to look up an existing slate, and `StarterCache.read` uses it to save a newly generated slate after ranking finishes.

*Call graph*: called by 2 (_held, read); 1 external calls (member_subject).


##### `claim_key`  (lines 128–129)

```
def claim_key(member_id: UUID) -> str
```

**Purpose**: Builds the storage key for the temporary claim that says a member’s slate is currently being generated. This prevents duplicate model work for the same member.

**Data flow**: It takes a member ID, converts it into the standard member subject string, and prefixes it with the starters claim label. The output is the storage key for the short-lived generation claim.

**Call relations**: `StarterCache._claim` uses this key while deciding whether this reader may generate a new slate. `StarterCache.read` deletes the claim after the generation attempt finishes.

*Call graph*: called by 2 (_claim, read); 1 external calls (member_subject).


##### `cooldown_key`  (lines 132–133)

```
def cooldown_key(member_id: UUID) -> str
```

**Purpose**: Builds the storage key for remembering that starter generation recently failed for a member. This helps the system avoid retrying too aggressively.

**Data flow**: It takes a member ID, converts it to the standard member subject string, and prefixes it with the cooldown label. The result points to the member’s recent failure stamp in storage.

**Call relations**: `StarterCache._may_generate` checks this key before allowing another model call. If generation fails, `StarterCache.read` writes a failure time under this key.

*Call graph*: called by 2 (_may_generate, read); 1 external calls (member_subject).


##### `_stamped`  (lines 136–145)

```
def _stamped(held: object, key: str) -> datetime | None
```

**Purpose**: Reads a timestamp from a small stored record, but treats bad or old-shaped data as missing instead of crashing. This makes cache housekeeping safe even if stored data is incomplete or from an older version.

**Data flow**: It receives some stored value and the name of the timestamp field to look for. If the value is a dictionary containing a readable ISO-format date string, it returns a datetime; if not, it returns none.

**Call relations**: `StarterCache._may_generate` uses it to read the last failure time from the cooldown record. `StarterCache._claim` uses it to read when an existing generation claim was created.

*Call graph*: called by 2 (_claim, _may_generate); 1 external calls (fromisoformat).


##### `StarterCache.read`  (lines 168–185)

```
async def read(self) -> Slate | None
```

**Purpose**: Runs the full start-screen slate flow for one member. It returns a fresh cached slate when possible, returns a stale slate when that is better than nothing, and generates a new slate only when it is safe and worthwhile.

**Data flow**: It starts with the current time, reads any stored slate, and checks whether it is fresh. If not fresh, it asks whether generation is allowed. If generation is not allowed, it returns the stored slate if there is one. If generation is allowed, it asks the model to rank starters, stores the new slate, clears the generation claim, and returns the new slate. If ranking fails, it records a cooldown, logs a warning, clears the claim, and returns the old slate.

**Call relations**: This is the main method callers use when the start screen needs rows. It calls `_held` to fetch cached data, `_may_generate` to decide whether to spend a model call, `_rank` to produce a new slate, and the key helper functions to update storage around success or failure.

*Call graph*: calls 6 internal fn (_held, _may_generate, _rank, claim_key, cooldown_key, starters_key); 2 external calls (now, warn).


##### `StarterCache._held`  (lines 187–194)

```
async def _held(self) -> Slate | None
```

**Purpose**: Loads the currently stored slate for this member, if it exists and still matches the expected shape. Bad stored data is ignored rather than shown or allowed to crash the screen.

**Data flow**: It builds the member’s starter storage key, reads that value from the shared store, and checks that it is a dictionary. It then validates the dictionary as a `Slate`. If validation succeeds, it returns the slate; otherwise it returns none.

**Call relations**: `StarterCache.read` calls this first so it can reuse a cached slate or fall back to it if regeneration fails.

*Call graph*: calls 1 internal fn (starters_key); called by 1 (read).


##### `StarterCache._may_generate`  (lines 196–206)

```
async def _may_generate(self, now: datetime) -> bool
```

**Purpose**: Decides whether this read is allowed to create a new slate. It blocks generation when there is no model, no remembered work to rank, no available balance to spend, a recent failure cooldown, or another reader already generating.

**Data flow**: It receives the current time and checks the cache object’s model, recalled memory, and solvency flag. It then reads the cooldown timestamp from storage and compares it with the cooldown window. If all checks pass, it asks `_claim` to reserve the right to generate. It returns true only if this reader should proceed.

**Call relations**: `StarterCache.read` calls this after finding no fresh slate. If `_may_generate` says yes, `read` moves on to `_rank`; otherwise it simply returns whatever slate was already stored.

*Call graph*: calls 3 internal fn (_claim, _stamped, cooldown_key); called by 1 (read).


##### `StarterCache._claim`  (lines 208–225)

```
async def _claim(self, now: datetime) -> bool
```

**Purpose**: Tries to reserve generation work for this reader, so two tabs or repeated polling do not pay for the same slate at once. It can also take over an abandoned claim after a short lease expires.

**Data flow**: It creates a claim record stamped with the current time. First it tries to insert that record only if no claim exists. If a claim already exists, it reads its timestamp. A recent claim means someone else is working, so it returns false. An old or unreadable claim can be replaced using the exact stored value as the comparison token, and the result of that replacement attempt is returned.

**Call relations**: `StarterCache._may_generate` calls `_claim` as the last gate before model generation. `_claim` uses `claim_key` to find the shared claim record and `_stamped` to understand whether an existing claim is still alive.

*Call graph*: calls 2 internal fn (_stamped, claim_key); called by 1 (_may_generate); 1 external calls (isoformat).


##### `StarterCache._rank`  (lines 227–254)

```
async def _rank(self, now: datetime) -> Slate
```

**Purpose**: Asks the language model to rank the best starter rows for this member. It packages the member’s memory, existing applications, and the product’s catalog into a structured request.

**Data flow**: It reads the cache object’s recalled memory, current application names, available unlock catalog, and selected model. It sends those to the model with strict instructions and a tool schema, meaning the model must respond through a named structured output. The model reply is then passed to `settle_slate`, which turns it into a validated `Slate`.

**Call relations**: `StarterCache.read` calls `_rank` only after cache, cooldown, balance, and claim checks pass. `_rank` hands the raw model message to `settle_slate` so the rest of the system receives a clean slate instead of trusting model output directly.

*Call graph*: calls 1 internal fn (settle_slate); called by 1 (read); 4 external calls (__init__, __init__, __init__, dumps).


##### `settle_slate`  (lines 257–290)

```
def settle_slate(reply: Message, generated_at: datetime) -> Slate
```

**Purpose**: Turns the model’s structured `record_slate` reply into a safe `Slate`. It keeps good ranked rows, drops bad or duplicate ones, and rejects a reply that did not actually record the required tool call.

**Data flow**: It receives a model message and the generation time. It searches the message content for the expected tool-use block. From that block, it validates each ranked entry, keeps only entries that name a known catalog unlock, removes repeats, and trims to the maximum allowed count. It separately validates the optional check-in, ignoring it if invalid. It returns a new `Slate` stamped with the current prompt digest.

**Call relations**: `StarterCache._rank` calls this after the model answers. If no required tool call is found, `settle_slate` raises an error, which lets `StarterCache.read` treat the generation as a failure and fall back to the stored slate if possible.

*Call graph*: called by 1 (_rank); 1 external calls (__init__).
