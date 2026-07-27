# System Handbook — Stage Index

Each stage below links to its full page; the paragraph is the stage's role in the system.

## [Operational entrypoints, command dispatch, and deployment preflight](stage-1.md) `stage-1` — 5 files

This stage is the system’s set of front doors and preflight checks. It is used when a person starts UFO, prepares it for deployment, runs administrator tasks, or proves that a sandbox is safe to serve traffic. The main user-facing door is core/src/ufo/cli.py, which defines ufoctl. This command-line tool lets people create a workspace, run it, inspect it, package it, and connect it to extensions. When packaging is needed, core/src/ufo/bundle.py gathers the current settings and chosen extensions into a self-contained folder, so the same setup can be rebuilt as a Docker image on another machine. For the hosted control service, control/src/ufo_control/main.py starts the web gateway and runs admin jobs such as database setup, invite creation, Slack retry work, and security setup. Before deployment, the sandbox checks act like test pilots. sandbox/mount_gate.py starts a real sandbox and proves the shared /workspace folder can be mounted and used. sandbox/proxy_gate.py proves the sandbox HTTPS proxy path works and returns the expected controlled response.

## [Capability discovery and extension activation](stage-2.md) `stage-2` — 14 files

This stage runs during startup, before the system begins its main work. Its job is to decide what extra abilities are allowed and make them available to the host. The extension store is like an app-store manager for the command-line tool: it can search a catalog, install an extension by recording it in a lockfile, or remove it. The lockfile is the trusted saved list of extensions that should be loaded.

The loader is the main switchboard. It reads the installed and pinned extensions, checks that the lockfile still matches what is present, then reads each extension’s manifest, which is a small description of what the extension contributes. From that it registers usable pieces such as tools, hooks, skills, credential slots, search or sandbox backends, sources, models, jobs, surfaces, and onboarding steps.

The many `__init__.py` files in extension folders mostly act as nameplates. They make folders importable Python packages, so the loader can reach extensions like Slack, web, research, sites, scheduled tasks, skill creation, and self-improvement.

## [Hosted gateway startup and workspace onboarding](stage-3.md) `stage-3` — 10 files

This stage is the front door for hosted UFO deployments. It runs during startup and before normal workspace use, bringing up a public gateway that helps a new user prove who they are and land in the right workspace. The gateway server is the main entry point. It guides users through installing the client, entering a work email, passing an invite gate if required, and receiving a signed-in token.

Several helper pieces act like the desks in a reception area. The claim code creates short-lived email codes, stores only safe hashed copies, and checks attempts and expiry. The email code decides whether an address is a real work email and sends codes through Amazon SES or local logs. The store keeps onboarding records in Postgres so the process survives restarts. Invite code logic enforces one-time workspace creation codes. Shared-domain logic finds or safely creates the workspace for a company domain.

The terminal and web helpers present the same flow to different users: byte directives for the UFO client, or browser pages and JSON. Finally, the onboarding module creates the first workspace, owner, default assistant, keys, and extension setup.

## [Serve process startup, app lifespan, and fleet coordination](stage-4.md) `stage-4` — 2 files

This stage is the server’s “come alive and stay healthy” step. It happens at startup, then keeps running behind the scenes until shutdown. The main entry point, core/src/ufo/serve.py, assembles the service process. It starts the web server, attaches extension routes and user-facing surfaces, connects the background job engine, sets up workspace boundaries, credentials, sandbox networking, storage, and live update channels. In simple terms, it plugs all the main parts together so requests, jobs, files, and real-time updates can move through the system safely.

core/src/ufo/runtime_instance.py makes each running server process known to the wider fleet, meaning the group of server processes working together. It writes heartbeat-style records so other parts of the system can tell the process is alive. It also runs cleanup loops that find work left behind by crashed or stopped processes, and it spreads cancellations from parent tasks to child tasks. During shutdown, these pieces help stop background server work cleanly instead of leaving loose ends.

## [Workspace identity, grants, credentials, and account connection](stage-5.md) `stage-5` — 16 files

This stage is the system’s identity checkpoint. It runs before tools act for a workspace and also supports later account-connection flows. First, workspace code decides which workspace a request belongs to, so database access, billing, and secrets stay in the right lane. Token code then creates signed proof strings: member bearer tokens, 30-day gateway tokens, short-lived artifact download tokens, and lower-level tamper-evident signing.

Next, credential code protects secrets. It encrypts stored values, checks private handoffs, shows which credential slots exist without showing the secret, and supports direct API-key connectors. Seat rules decide which members are allowed to use the agent when capacity is limited.

The connection pieces let users safely attach outside accounts. Grants record a safe account identifier, while connector objects let people list, inspect, share, or revoke connected accounts. Browser callback routes finish approval flows after an outside service sends the user back. Composio and Pipedream act as bridges to hosted connector services. Slack, GitHub, and YC-style login flows follow the same pattern: prove the user approved access, then store only the safe connection handle needed for future tool use.

## [Surface ingress, admission, and user-facing request handling](stage-6.md) `stage-6` — 8 files

This stage is the system’s front door. It is used when a person talks to UFO from Slack, a web browser, or the terminal, and when a scheduled or uploaded item needs to enter the same flow. Its job is to turn outside events into the core system’s standard pieces: members, conversations, messages, files, and agent turns.

The Slack surface translates Slack messages, buttons, file uploads, installs, and replies into UFO actions. The terminal surface receives HTTP messages from the shell client and streams simple text updates back. The web surface lets a signed-in browser send chat messages, watch live replies, and see workspace spending. The shared surface bridge gives these interfaces trusted ways to identify users, admit messages, read credentials, stream progress, and send final replies.

The admission code is the gatekeeper. It checks permission, avoids duplicate messages, links the message to the right conversation and agent, saves it safely, and queues work only when appropriate. The agents file exposes the workspace agent settings. The artifacts endpoint securely serves shared files using signed download tokens.

## [Turn queueing, live streams, and worker dispatch](stage-7.md) `stage-7` — 4 files

This stage is part of the main work loop, after an incoming message has been accepted. The message is now a “turn”: one unit of agent work stored durably, meaning it is kept in the database so it survives crashes and can be picked up later. core/src/ufo/loop/queue.py is the dispatcher. It claims a waiting turn from the queue so only one worker runs it, rebuilds the needed runtime setup, opens the sandbox where tools can run safely, and hands the turn to the agent loop.

While that work runs, interfaces need to watch it. core/src/ufo/hub.py is the local live feed for one turn, sending small update “frames” such as text chunks, tool use, costs, and final results. It also supports reconnecting from a saved position. extensions/redis_hub/.../stream_hub.py provides the same kind of feed across multiple server processes using Redis Streams, a shared message pipe. core/src/ufo/surfaces/hub_tail.py ties this together for clients, combining fast live frames with database status checks so watchers know when a turn has paused or finished.

## [Conversation context, prompt construction, and model selection](stage-9.md) `stage-9` — 11 files

This stage is the “packing desk” before each model call in the main work loop. It gathers the conversation, prepares the instructions, chooses the right AI model, and formats the request so the chosen provider will accept it. The prompt package starts in ufo.loop.prompts: the package marker makes the prompt tools importable, and render.py fills the prompt template, checks that no blanks were left behind, and records a fingerprint so changes can be traced. governance.py adds safety around prompt edits by requiring proposed changes to be approved before they replace an agent’s instructions.

Long conversations are handled by compaction.py. It keeps recent messages as they are, summarizes older ones, and saves both versions so the system can inspect what changed.

The model files act like a travel guide and adapter kit. spec.py defines what facts are tracked for each model. catalog.py lists built-in models. registry.py lets the system look up a model and build the right client. interface.py defines the shared request shape and trims oversized images. openai.py, anthropic.py, and the OpenRouter extension translate that shared shape to each provider’s API and translate streamed replies back.

## [Agent turn engine main loop](stage-10.md) `stage-10` — 2 files

This stage is the main work loop for one assistant turn. It starts after a user message is ready to be answered, and it ends when the assistant has either produced a final result, paused, or handed work to another agent. The key file is `engine.py`, which acts like a traffic controller. It claims the turn so only one worker handles it, gathers the conversation history, sends it to the model, streams the model’s answer as it arrives, and watches for tool calls. When the model asks to use a tool, the engine runs it safely, records what happened, and feeds the result back into the conversation.

The engine also handles interruptions. It can pause for a user question, stop when a spending limit is reached, retry when the conversation is too large, or absorb new messages that arrived mid-turn. It records usage for billing and saves progress carefully, so if the system crashes it can replay the turn without repeating paid model calls or tools with real-world side effects. `__init__.py` simply makes this folder importable as a Python package.

## [Tool catalog, dispatch, and built-in work actions](stage-11.md) `stage-11` — 52 files

This stage is the system’s tool room during the main work loop. When the AI asks to use a tool, the registry checks that the tool exists and that its input has the right shape. The context then gives the tool only the powers it is allowed to use, like workspace access, account choices, cleanup hooks, or permission checks. The built-in tools use that context to run safe shell commands, edit files, share files, ask the user questions, load skills, connect accounts, or hand work to subagents.

Several tool families plug into this same catalog. Connector tools act as guarded adapters to outside services and apps. Research and memory tools look up web pages, papers, search results, or stored knowledge. Document tools inspect, repair, comment on, and export office files and PDFs. The todo extension keeps a per-conversation checklist so longer tasks can continue across turns. The object system lets extensions expose named workspace records in a controlled way, while conversations are exposed only as read-only metadata. Together these parts turn a model’s request into a checked, limited, recorded action.

### [External connector and brokered app tools](stage-11.1.md) `stage-11.1` — 18 files

This stage is shared behind-the-scenes support for letting the agent use outside apps safely. It is like a plug adapter and security desk between the agent and services such as GitHub, Slack, Gmail, YC, or test-only fake apps.

The core connector contract defines the common “plug shape”: how tools are discovered, run through a broker, and given credentials without exposing secret tokens. The generic connector tools let the agent find and call these tools, pass files in and out, and trim bulky results so they fit in a conversation.

Composio and Pipedream each provide a broker, client, and proxy. The client talks to the outside broker service, the broker presents its tools to UFO in the common format, and the proxy rewrites normal web requests so UFO never sees the user’s raw provider token. Composio also has a resolver and MCP search session for finding toolkits dynamically.

The MCP extension connects to workspace-configured tool servers. Slack tools guide setup and conversation lookup. YC tools safely call YC’s command-line system. The evaluation environment supplies predictable fake email, calendar, and code-search connectors for tests. Empty package files simply make these extension folders importable.

### [Research, search, recall, and knowledge lookup tools](stage-11.2.md) `stage-11.2` — 4 files

This stage is the system’s information desk. It does not change the workspace; it helps an agent look things up while it is working. Some tools search the public web, some fetch a specific page, and some recall stored memories from earlier user or conversation context.

The shared base is core/src/ufo/search.py. It defines a common “search and fetch” interface, meaning the rest of the code can ask for web results without caring which search company provides them. extensions/exa/ufo_ext_exa.py connects that interface to Exa, an external web search service. It uses an API key supplied by the workspace, while keeping that key away from the sandboxed agent code.

extensions/research/ufo_ext_research/tools.py is the toolbox the agent actually sees. It turns requests like “search the web,” “fetch this page,” or “find academic papers” into backend calls, then formats the results as readable text. core/src/ufo/memory.py does the same kind of unifying work for remembered information, so different memory stores can all be searched in one consistent way.

### [Document, office, PDF, and artifact automation tools](stage-11.3.md) `stage-11.3` — 23 files

This stage is a shared document toolbox for the system. It is not one main work loop. Instead, other parts call these tools when they need to open, fix, inspect, update, or export office-style files.

The review tools keep a record of document issues and write those notes back into real files, such as PDF, PowerPoint, and Excel, so people can see the feedback in familiar programs. The Word tools treat a DOCX file like a zipped box of smaller XML text files: they unpack it, add comments, accept tracked edits through LibreOffice, and pack it back up. The PowerPoint tools do the same kind of unpacking and reboxing for PPTX files, with extra repair, slide cleanup, and thumbnail helpers. The Excel tool uses LibreOffice invisibly in the background to recalculate formulas and check for errors. The PDF tools inspect forms and page layout, fill fields or place text, and turn pages into images.

The package __init__.py file is just the doorway that lets Python import this document toolkit cleanly.

#### [Document review state and artifact annotation scripts](stage-11.3.1.md) `stage-11.3.1` — 7 files

This stage is the document review’s record keeper and “write it back” toolset. It is used after or during the review, not to judge the document itself, but to save what was found and place those findings back into files people can open.

The manage_state.py script is the control panel. It stores review progress, claims, issues, and the final summary in a JSON file, which is a simple text format for structured data. It also writes a log so actions can be traced later. constants.py keeps the shared filenames for that state and log in one place. models.py defines what an issue looks like, such as its location and message, and turns it into readable comment text. __init__.py simply lets these scripts be imported as a package.

The annotation scripts are the exporters. annotate_pdf.py adds highlights and sticky-note comments to PDFs. annotate_pptx.py writes issues as PowerPoint comments. annotate_xlsx.py copies an Excel workbook and adds cell comments. Together, they turn saved review notes into visible feedback inside the original kinds of documents.

#### [Word DOCX unpacking, packing, comments, and tracked changes](stage-11.3.2.md) `stage-11.3.2` — 4 files

This stage is Word-specific behind-the-scenes support for working with DOCX files. A DOCX file looks like one document, but it is really a zipped package of many XML files, which are text files that describe the document’s content and settings. The unpack script opens that package into a folder and tidies the main document XML so later tools can read, edit, or compare it without extra noise.

Once unpacked, the comment script can add a Word comment. It does not just write one note. It updates several hidden XML parts and links between them, because Word needs those records to know where the comment belongs and how to display it.

The pack script then reverses the process. It cleans unnecessary spacing in the XML and zips the folder back into a normal DOCX file Word can open.

The accept_changes script handles another Word task: tracked changes. It runs LibreOffice in the background, accepts all edits, and saves a clean copy without showing a window.

#### [PowerPoint PPTX packaging, repair, slide, and thumbnail tools](stage-11.3.3.md) `stage-11.3.3` — 5 files

This stage is a set of PowerPoint workshop tools. It is not the main app loop; it is behind-the-scenes support used when a presentation must be inspected, changed, repaired, or previewed. A .pptx file is really a zipped bundle of many smaller files, including XML files that describe slides and text. unpack.py opens that bundle into a normal folder so people or automation can edit those pieces directly. pack.py does the reverse: it gathers the folder back into a .pptx file and tidies XML spacing without damaging slide text. repair.py fixes known problems in presentations created by pptxgenjs, a library that generates PowerPoint files, so PowerPoint will not complain or alter text when opening them. slides.py is the practical toolbox: it can remove unused files from an unpacked presentation, add a slide, or create contact-sheet thumbnails for quick visual review. __init__.py simply makes these scripts importable as a package. Together they act like unpacking, fixing, organizing, and reboxing a presentation.

#### [Excel XLSX recalculation through LibreOffice](stage-11.3.4.md) `stage-11.3.4` — 3 files

This stage is a behind-the-scenes tool for Excel spreadsheet work. It is used when the system needs an .xlsx workbook to have fresh formula results, but does not want to show a spreadsheet window to a user. It uses LibreOffice in “headless” mode, meaning LibreOffice runs invisibly in the background like a worker in a back room.

The main worker is recalc.py. It opens the Excel file, tells LibreOffice to recalculate every formula, saves the workbook again, and checks whether any cells still show Excel error values such as failed calculations. It then gives a clear success-or-error result to the rest of the system.

The _soffice.py file is the helper toolbox. It contains the shared details for starting LibreOffice safely without a visible desktop window, and it knows where LibreOffice keeps user macro files on Linux and macOS. The empty __init__.py file simply makes this scripts folder importable by other Python code, so these tools can be reused cleanly.

#### [PDF form filling, layout inspection, and rendering tools](stage-11.3.5.md) `stage-11.3.5` — 3 files

This stage is a set of PDF command-line tools used when the system needs to understand, prepare, or display PDF documents. It is not the main work loop itself. It is shared support for document tasks, like a small toolbox used before or during form processing.

The form filling tool works with PDFs that already contain fillable fields. It can find those fields, describe them in JSON, which is a simple text format for structured data, and later fill the PDF using values from JSON.

The layout tool is for static PDFs that do not have built-in form fields. It inspects where things appear on the page, previews where new fill areas should go, and can place text onto the PDF as annotations. In effect, it helps mark up a plain paper-like document so the system can fill it reliably.

The render tool converts PDF pages into PNG image files. These images can be used for viewing, checking layout, or image-based processing. Together, the tools let the system inspect, fill, mark up, and visually render PDFs.

## [Sandbox lifecycle, workspace storage, and egress control](stage-12.md) `stage-12` — 42 files

This stage is the safety shell around work that can affect the outside world. It supports the main work loop whenever the agent runs code, edits files, opens a browser, or reaches the internet. The sandbox session layer defines the boundary: tools see only the conversation’s workspace, while the actual carrier can be local execution, Docker, or E2B cloud sandboxes. The template builder keeps the Docker and E2B environments made from the same recipe.

Workspace storage is the shared filing cabinet. Blob storage hides whether bytes are local or in S3. Artifacts are the files intentionally shared back for later use. Short-lived storage credentials and mount-command generation let a sandbox attach its /workspace folder to only its own storage area.

The local, Docker, and E2B carriers start, resume, talk to, copy files into or out of, and clean up sandboxes. Browser sessions and coding, website, and REPL helpers are the tools used inside that boundary.

Network access passes through the proxy. Its rules decide what traffic is allowed, when secrets may be injected, and when broker forwarding is needed. The proxy server enforces those choices and records audit and billing data.

### [Browser and computer-use sessions](stage-12.1.md) `stage-12.1` — 25 files

This stage is the browser-use part of a turn. It lets the agent use Chrome much like a person would: open pages, inspect what is visible, click, type, upload files, download files, and then clean up when the turn ends. It is both part of the main work loop and shared support behind the scenes.

First, the endpoint providers supply a Chrome to control, either from Browserbase, a hosted browser service, or from a sandboxed local Chrome. They hide where the browser lives. Next, the CDP transport is the control wire to Chrome. CDP, or Chrome DevTools Protocol, is Chrome’s remote-control language. It sends commands, receives events, runs small page scripts, handles pop-up dialogs, and waits until pages settle.

On top of that, session orchestration and agent tools keep one shared browser session for the turn and expose safe actions to the agent. Page inspection is the system’s eyes: it reads the page, builds a simpler map of text and controls, and connects model-chosen elements back to real screen locations. Finally, the action modules are the hands: tabs, clicks, typing, forms, uploads, downloads, scrolling, and screenshots.

#### [Browser endpoint providers and package shells](stage-12.1.1.md) `stage-12.1.1` — 5 files

This stage is behind-the-scenes support for browser automation. Its job is to give the rest of the system a usable Chrome connection without making the core code care where Chrome is running. The shared contract lives in core/src/ufo/browser.py. It defines the idea of a browser endpoint: an address for Chrome’s DevTools Protocol, which is Chrome’s remote-control socket for inspecting pages and sending browser commands.

Two providers can satisfy that contract. extensions/browserbase/ufo_ext_browserbase.py connects to Browserbase, a hosted browser service. It reads a saved connection URL and hands that URL to the browser engine. extensions/sandbox_chrome/ufo_ext_sandbox_chrome.py does the local sandbox version. For each conversation, it starts or reuses a Chrome running inside the sandbox, exposes its debugging address in a controlled way, and returns that address.

The remaining files are package shells. extensions/browser/ufo_ext_browser/__init__.py advertises the browser extension package and its tools. extensions/browser/ufo_ext_browser/bua/__init__.py simply makes its folder importable.

#### [CDP transport, runtime, and browser settling](stage-12.1.2.md) `stage-12.1.2` — 5 files

This stage is the browser automation “control room.” It sits behind the main work loop, especially after actions like clicking, typing, or opening a page. Its job is to talk to Chrome, run small bits of page code, avoid freezes, and decide when it is safe to move on.

The cdp.py file is the main phone line to Chrome. It opens a WebSocket, which is a long-lived two-way connection, sends commands, receives replies, and passes browser events to the code waiting for them. wire.py checks the shape of the JSON messages on that line, so the rest of the system gets clean, predictable Python data instead of raw, uncertain input.

runtime.py uses that connection to run JavaScript inside a tab and translates the result back into normal Python values, or raises a clear error if the page code fails. dialogs.py watches for pop-up alerts and accepts or dismisses them before they block everything. settle.py is the patience timer: it waits for real page work to finish while ignoring endless background noise like ads or analytics.

#### [Browser session orchestration and agent tools](stage-12.1.3.md) `stage-12.1.3` — 6 files

This stage is the browser “workbench” the agent uses during a turn. It is shared behind-the-scenes support for browser tasks, not the whole main loop. When the agent decides to open a page, click, type, read text, or download a file, these files provide one safe path from the agent’s request to the live Chrome browser.

The actions file defines the common vocabulary for browser moves, with clear shapes for commands like click, type, scroll, screenshot, and wait. The tools file exposes those moves as callable tools for the agent and makes sure they reuse the same browser connection during the turn. The backend is the tool-facing surface: it performs navigation, tab work, uploads, downloads, and page reading, opening Chrome only when needed. The session file is the central hub underneath, holding the live Chrome connection, watching browser events, and offering higher-level browser actions. Before any action runs, fixup cleans small model mistakes, such as typing before focusing a field. Errors defines a special signal for impossible model output, such as naming an element that is not there.

#### [Page inspection and element mapping](stage-12.1.4.md) `stage-12.1.4` — 4 files

This stage is the system’s “eyes” for a live web page. It runs during the main work loop, whenever the system needs to understand what is on the screen before deciding what to click, type, or report. It reads the page, turns it into a simpler form an AI model can use, and then maps the model’s answer back to the real browser.

content.py is the command doorway. It accepts simple JSON-like requests such as “get the page text” or “find this element,” performs the lookup, and returns safe, limited-size results. page.py builds the main page snapshot. It reads the live browser state and creates a structured description of controls, text, and positions, then can turn an element reference back into a real place to act on. find.py searches that structured “accessibility tree,” a browser-made outline of visible text and controls, and can check whether an AI’s chosen element really exists. coordinate.py is the ruler. It converts between AI image coordinates and actual browser pixels so clicks land in the right spot.

#### [Browser actions, input, tabs, forms, and downloads](stage-12.1.5.md) `stage-12.1.5` — 5 files

This stage is part of the system’s main work loop. It is where the agent acts like a person using a browser: opening tabs, clicking buttons, typing text, filling forms, scrolling pages, uploading files, and saving downloads. It sits above the low-level browser connection and turns simple instructions into exact browser actions.

The tabs module is the page manager. It opens, closes, switches, and navigates tabs by sending Chrome DevTools Protocol messages, which are structured commands that Chromium browsers understand. The computer module is the main “hands and eyes” layer. It performs actions such as clicking, typing, and scrolling, then reports what the page looks like afterward, including screenshots and safety notes. The keys module is the keyboard translator. It converts text and shortcuts like Ctrl+C into realistic key press messages. The forms module focuses on web forms, safely setting field values and attaching files for upload. The downloads module watches for saved files, can force files like PDFs to download instead of opening in the browser, waits for them to finish, and makes them available to the rest of the system.

### [Coding, website, and REPL execution helpers](stage-12.2.md) `stage-12.2` — 3 files

This stage provides practical helper tools for work that happens inside a sandbox, which is a safe, isolated workspace where code can run without touching the user’s real machine. It supports the main work loop: writing code, testing ideas, running small experiments, and checking websites.

The coding package file is a simple doorway. It tells Python that the coding extension folder can be imported by the rest of the system. It has no moving parts itself, but it lets other coding features be found and used.

The REPL manifest describes tools for running short Python or JavaScript snippets. A REPL is an interactive scratchpad for code. Here, successful variables and imports can stay available between calls, so the agent can build up an analysis step by step instead of starting over each time.

The website tools are the site workshop. They can build files, start a local server, deploy static sites, or publish web apps in the sandbox. They also check that a server is truly reachable before saying it worked.

## [Skills, user-created skills, and subagent orchestration](stage-13.md) `stage-13` — 16 files

This stage is shared support for giving the agent extra abilities and extra helpers. A “skill” is a reusable folder of instructions and files. The skills package marker makes that code importable, while the runtime reads skill folders, checks which other skills they need, and copies the right files into the sandbox workspace, which is the safe working area for a run. The model catalog skill builds a live reference table of available AI models. The sample skill probe is a simple test that proves a skill can run.

User-made skills are handled by the skill creation extension. Its manifest exposes create, list, inspect, update, and delete actions. Its store saves skills per workspace, checks they are valid and limited, and keeps them separate from built-in skills.

The other half is delegation. Subagent profiles describe specialized child agents, and the subagents loop starts them, waits for them, messages them, or cancels them. Browser, research, and website extensions define focused helper agents plus tools for handing work to them. The brief pipeline defines outline, draft, and critique writing stages.

## [Source synchronization and page lifecycle](stage-14.md) `stage-14` — 57 files

This stage is the system’s import pipeline. It runs during background or user-requested syncs, when UFO contacts outside services, fetches their records, and turns them into stored pages that can later be searched, remembered, or used for alerts.

At the center is the core synchronization framework. It defines the common connector contract, walks through large services in pieces, follows page-by-page web API results, retries requests, and records what changed or disappeared. The registry and source tools keep the catalogue of available connections, name each connected account consistently, expose synced pages as read-only workspace pages, and notify subscribed conversations when pages change. Special sources, such as YC and evaluation connectors, plug into the same path.

Around this engine are many connector families. Google Workspace, collaboration tools, developer tools, CRM and support systems, marketing platforms, HR systems, finance tools, and commerce services each have adapters that understand their own vendor’s API. Together, they act like translators feeding one shared machine: fetch outside data, normalize it, store page changes, and report the sync result.

### [Google Workspace source connectors](stage-14.1.md) `stage-14.1` — 6 files

This stage is the set of Google Workspace “source connectors.” A connector is a small adapter that talks to an outside service and reshapes its data into the system’s own searchable pages. It is part of the main syncing work: after an account is connected, these files fetch Google records, notice what changed, and pass clean text and metadata to the rest of the system.

The Gmail connector reads mailbox messages, turns them into plain searchable text, and uses change tracking so later syncs only fetch new or deleted mail. Google Calendar does the same for primary-calendar events and attendee lists. Google Docs reads accessible documents and extracts their text without ever editing them. Google Drive is the broad file cabinet connector: it streams files, shared drives, permissions, comments, revisions, and deletions from Google’s paged API results. Google Meet focuses on meeting artifacts, such as transcripts and Gemini notes, and turns them into readable pages. Google Sheets finds spreadsheets through Drive, splits them into tabs, and can read the rows inside each tab.

### [Collaboration, knowledge, and scheduling source connectors](stage-14.2.md) `stage-14.2` — 7 files

This stage is a set of read-only connectors. They sit near the start of the system’s work, where outside services are contacted and their data is turned into a common shape for later syncing, indexing, and search. Each connector is like an adapter plug for a different workplace tool.

Airtable reads bases, tables, and records, flattening Airtable’s nested structure into steady item streams. Calendly reads scheduling data such as users, event types, booked events, members, and invitees. Confluence reads Atlassian spaces, pages, blog posts, comments, groups, and audit records, and turns them into readable text. Microsoft Teams uses Microsoft Graph, Microsoft’s web doorway for app data, to collect teams, channels, chats, and messages. Notion reads users, pages, databases, blocks, and comments, preserving knowledge in a searchable form. Outlook also uses Microsoft Graph to read mail, threads, contacts, calendars, and folders, while tracking progress so future runs can resume efficiently. Slack reads users, conversations, messages, threads, and senders. Together, these files bring human communication and knowledge into the system without writing back to the original tools.

### [Work management and developer operations source connectors](stage-14.3.md) `stage-14.3` — 9 files

This stage is shared behind-the-scenes support for syncing work and engineering tools into the system’s searchable memory. Each file is a connector, meaning a small adapter that knows how to talk to one outside service, read its web API, and turn the replies into standard streams of records the rest of the system can store, search, and resume later.

The project-management connectors cover Asana, ClickUp, Jira, Linear, monday.com, and Wrike. They fetch things like workspaces, teams, projects, boards, tasks, issues, comments, users, labels, workflows, and custom fields. Each one understands that service’s shape: ClickUp walks from teams down to lists, while Linear and monday.com use GraphQL, a query language for asking an API for selected data.

The developer-operations connectors cover GitHub, PagerDuty, and Sentry. GitHub brings in repositories, issues, pull requests, commits, and comments. PagerDuty brings in incidents, services, schedules, and on-call records. Sentry brings in error reports, events, releases, and projects. Together, these connectors act like translators for the main sync engine.

### [CRM, sales, and customer support source connectors](stage-14.4.md) `stage-14.4` — 6 files

This stage is the set of “adapters” that let the system bring in customer and sales data from outside services. It sits at the edge of the sync engine: these files talk to vendor APIs, meaning the web doors those services provide, then reshape the answers into standard records the rest of the code can store, search, and track.

Each connector knows the habits of one service. Attio reads companies, people, deals, tasks, notes, meetings, and call recordings. HubSpot covers a wide range, from CRM objects and deleted records to marketing assets, conversations, lists, permissions, and links between objects. Salesforce reads common business records such as accounts, contacts, opportunities, and cases, without writing anything back. Freshdesk focuses on helpdesk data and carefully follows its different page-by-page formats. Intercom turns conversations, contacts, companies, tickets, admins, and tags into one common stream. Zendesk does the same for tickets, users, organizations, help articles, and community posts. Together, they act like translators for customer-facing systems.

### [Marketing, advertising, and lifecycle source connectors](stage-14.5.md) `stage-14.5` — 7 files

This stage is a set of source connectors: small adapters that let the system pull data from outside marketing tools. It is part of the main syncing work, not startup or shutdown. Each connector knows how to sign in to one service, ask for the right kinds of data, handle pages of results, and turn the replies into standard records the rest of the system can store, search, and reuse.

ActiveCampaign brings in marketing and customer-relationship objects, while safely skipping API areas that refuse access. Facebook Ads reads Meta ad accounts, campaigns, ad sets, ads, and daily performance results. Google Ads does the same for Google advertiser accounts, including campaigns, ad groups, ads, and metrics. Instagram uses Meta’s API to collect business pages, linked accounts, posts, stories, and analytics. Klaviyo covers email and ecommerce marketing data such as profiles, lists, campaigns, events, forms, catalog items, and webhooks. Mailchimp reads audiences, subscribers, campaigns, reports, tags, and email activity. Typeform brings in forms, responses, workspaces, themes, images, and webhook settings. Together, they act like translators for different marketing systems.

### [HR, recruiting, and workforce source connectors](stage-14.6.md) `stage-14.6` — 6 files

This stage is part of the system’s data-gathering layer. It runs when a sync job needs to bring in people-related records from outside services. Each connector knows how to talk to one vendor’s web API, meaning the online doorway that service provides for software to request data. The connector asks for records page by page, then presents them as steady streams the rest of the system can store, search, or reuse.

Ashby, Greenhouse, and Recruitee cover recruiting. They pull hiring records such as candidates, jobs, applications, interviews, offers, departments, and users. BambooHR covers employee administration, including directories, employee details, time off, timesheets, field definitions, and reports. Deel covers contractor and HR operations, such as contracts, forms, payslips, timesheets, and tasks. Rippling covers workforce structure, including companies, workers, and teams.

Together, these files act like adapters for different plug shapes. Each outside system is different, but this stage makes their data look consistent enough for the shared sync machinery to process.

### [Finance, billing, and commerce source connectors](stage-14.7.md) `stage-14.7` — 7 files

This stage is the system’s set of “cash register and ledger” connectors. It is used during syncing, when the system reaches out to outside services and brings back financial or commercial records in a standard shape. Each file is a translator for one service’s web API, meaning the online doorway the service provides for software to request data.

The Brex connector reads spend data like transactions, expenses, vendors, budgets, and departments. Chargebee and Recurly focus on subscription billing, turning customers, subscriptions, invoices, and payments into record streams. QuickBooks and Xero cover accounting data, such as accounts, contacts, invoices, and payments. Square reads commerce data like customers, orders, catalog items, locations, payments, and inventory. Stripe handles a wide range of payment and billing objects, including customers, charges, invoices, subscriptions, and connected accounts.

Together, these connectors do the same basic job: authenticate, ask the right endpoint for each kind of object, follow paged results, and feed clean batches of records into the rest of the sync system.

### [Source extension registry, tools, pages, and special sources](stage-14.8.md) `stage-14.8` — 4 files

This stage is shared support for bringing outside information into a workspace and making it usable after it has been synced. It is not the main work loop itself; it is more like the cabinet and label maker for connected sources.

The registry file is the master catalogue of built-in connectors, such as Slack, GitHub, Stripe, and Google Drive. It also gives each connected account a stable internal name, so the same account can be recognized reliably later. The tools file defines a “source,” meaning a saved connection to an outside service and the data streams the system should keep in sync. It also watches for changed synced pages and alerts subscribed conversations. The pages file presents synced documents as read-only workspace pages, so callers can list them, read a safely limited copy, or let the workspace owner forget one. The YC source file is a special connector for Y Combinator information, supporting trusted guidance collections and limited directory-style searches that can be registered for syncing.

### [Core source synchronization framework](stage-14.9.md) `stage-14.9` — 5 files

This stage is the shared engine for bringing outside content into UFO. It sits behind the scenes during regular syncing, between connectors that talk to services like GitHub or Zendesk and the stored page records used later for search or recall.

The package marker, __init__.py, simply makes this folder importable by the rest of the program. connector.py defines the common contract that every connector must follow: what it can fetch, how records are grouped into pages, and how to walk through separate partitions such as repositories, projects, or channels while keeping progress. rest.py supplies common tools for connectors that call REST APIs, meaning web services reached through standard HTTP requests. It provides safe request handling, retries, and pagination, which is the process of reading results a page at a time. backend.py turns raw connector streams into a single sync outcome the system can understand. sync.py registers sources, runs fetches, stores changed pages, marks vanished pages as deleted, and exposes those changes for later indexing.

## [Derived indexing, memory, graph extraction, and page alerts](stage-15.md) `stage-15` — 15 files

This stage runs behind the scenes after pages or saved artifacts change. Its job is to turn raw text into things the system can find, remember, and act on later. The core indexing file defines the common shape of text “chunks,” search results, and index backends. The OpenAI embedding extension turns text into numeric meaning fingerprints, splitting large text safely. The default index stores chunks and embeddings locally, while the Turbopuffer extension can store and search them in an external search service.

The memory extension builds on this. Its manifest wires in tools, hooks, jobs, and screens. Its store saves memories, searches them, and keeps indexes current. The condenser turns changed pages into durable facts and later merges related facts into clearer summaries. The objects file lets memories be opened and inspected read-only, while events gives shared names for recall activity.

The knowledge graph extension adds another recall path: its manifest connects hooks and lookup tools, and its store extracts entities and relationships from pages. Page alerts let users watch topics, then send a follow-up conversation message when a changed page matches.

## [Scheduled jobs, billing automation, and self-improvement loops](stage-16.md) `stage-16` — 15 files

This stage is the system’s night shift: background work that happens outside a user’s live request. The core job machinery finds which workspaces need attention, schedules future work, and lets workers safely claim jobs so the same task is not run twice. The scheduled-tasks extension adds clock rules, including cron schedules, plus tools to create, edit, run, or delete tasks. Its runner wakes up on ticks, starts due tasks, skips expired ones, and reschedules repeating ones. It can also pause a workflow until a person replies or a timer ends.

Other jobs keep the business side moving. The Slack Connect job creates customer channels and invitations safely, even after retries. The Metronome extension syncs usage, billing, Stripe setup, and seat counts, and gives owners chat tools for billing and seats.

The self-improvement loop works like a cautious training lab. It mines past failures into test examples, asks a model for prompt changes, replays old conversations without causing real-world side effects, grades the results, and uses statistical checks before opening a governed improvement proposal.

## [Reply delivery, terminal state commit, cleanup, and shutdown](stage-17.md) `stage-17` — 1 files

This stage is the system’s “finish line” for a piece of work. After the assistant has produced an answer, stopped early, failed, or been cancelled, the system must make that ending visible and safe. It sends the final reply back to the place that asked for it, such as Slack, the web app, or a terminal. It also closes any live output stream, records the final status, shares any signed files or artifacts, and cleans up temporary resources like browser sessions, tools, sandboxes, and background callbacks. During shutdown, it also tries to leave no half-finished work behind, and later startup can recover abandoned work safely.

The directly included file, `cancellation.py`, provides the shared “stop button” for a running turn. It first tells the active workflow to stop, so child tasks and tools can wind down. Only after that does it mark the turn as cancelled in the database, keeping the recorded state in step with what actually happened.

## [Declarative configuration, packs, and capability manifests](stage-18.md) `stage-18` · (cross-cutting) — 32 files

This stage is the system’s catalog and instruction sheet. It mostly runs during startup and shared setup, before the assistant begins real work. It tells the runtime what bundles, tools, skills, credentials, outside services, and helper agents exist. Later lifecycle stages decide which of these to actually turn on.

The pack recipes are like pre-packed toolboxes for different jobs, such as a local developer assistant, a hosted assistant, evaluations, or a chief-of-staff assistant. The agent and workflow manifests label the tools inside each extension, such as browser work, coding, documents, research, sites, or brief writing. Platform manifests register shared surfaces like web pages, command-line access, scheduled tasks, alerts, debugging, and optional Redis-based shared state. Provider and connector manifests describe outside doors and keys, including Slack, Bedrock, OpenRouter-style model providers, YC tools, API-key connectors, and login-based app integrations.

At the center, core/src/ufo/config.py loads the deployment configuration from one TOML file, a simple structured settings file. It checks required and safety-sensitive settings early, so the server fails fast instead of starting in a broken or unsafe state.

### [Pack recipes and pack package declarations](stage-18.1.md) `stage-18.1` — 10 files

This stage is a set of recipes for starting the system in different modes. A “pack” is a named bundle: it does not build the tools itself, but tells UFO which existing extensions, skills, onboarding steps, and service choices to load together. Think of it like choosing a pre-packed toolbox before work begins.

The local assistant pack turns on the full developer assistant experience, while the billing assistant pack adds billing setup for local testing. The hosted assistant pack selects versions meant to run on managed services. The assistant evaluation pack uses fake email, calendar, and code-search tools so tests can run safely without real integrations.

Other packs target specific jobs. The chief-of-staff pack loads Slack, meetings, notes, memory, tasks, and follow-up workflows for a manager-style assistant. The DSQA and GDPVal files define evaluation toolkits with different search or browser capabilities. The sample pack is a small end-to-end proof that packs can add an extension, a skill, and onboarding. The YC package marker makes its folder importable, and its manifest defines the YC founder pack’s name, version, dependencies, and skills.

### [Agent, skill, and workflow extension manifests](stage-18.2.md) `stage-18.2` — 6 files

This stage is behind-the-scenes setup support. It does not do the agent’s work directly. Instead, each manifest acts like a label on a toolbox, telling the larger system what tools, helper agents, prompts, skills, and outside services are available when an extension is turned on. A subagent is a specialized helper agent for one kind of job.

The brief-pipeline manifest sets up a writing assembly line, with helpers for outlining, drafting, and critiquing, plus a skill package for making briefs. The browser manifest advertises browser tools, a browser-focused subagent, prompt text, and needed external services. The coding manifest registers a coding subagent, its skills and tools, and the GitHub credentials it may safely use. The documents manifest lists skills for creating, editing, reviewing, and styling files such as documents, slides, spreadsheets, PDFs, and themes. The research manifest adds research tools, research subagents, prompts, and optional skills. The sites manifest registers website-building tools, a site-building subagent, prompts, and related skills.

### [Platform surface and runtime extension manifests](stage-18.3.md) `stage-18.3` — 8 files

This stage is part of startup and shared platform support. It is made of “manifest” files, which are like registration cards. Each one tells the core system that an extension exists, what it is called, and what doors it wants to open.

The connectors manifest advertises connector tools, their shared object type, and prompt text so the host can offer them to users or agents. The debugger manifest registers debugger web and API access. The ufo manifest exposes the shell-client surface, letting a command-line client talk to the system. The web manifest adds normal web routes so browser-facing pages can be mounted. The page-alerts manifest lists chat tools and a background event listener for page alert activity. The scheduled-tasks manifest registers recurring task objects, a wait tool, a clock-based runner, and scheduling skills. The self-improvement manifest adds a daily job that evaluates and improves prompts using model access. The Redis hub manifest registers an optional Redis-backed hub, so live shared state can move through Redis instead of staying inside one server.

### [External provider, credential, and connector manifests](stage-18.4.md) `stage-18.4` — 7 files

This stage is shared setup material. It does not do the main work itself. Instead, it tells the platform what outside services exist and how they can be used, like a directory of doors, keys, and sign-in desks.

The Bedrock file adds Amazon Bedrock model choices, including Anthropic and OpenAI-style models, with their costs, limits, and the client code needed to call them. The Composio and Pipedream manifests announce app connectors and the web routes used to finish user approval, such as an OAuth sign-in flow, which is a “log in and grant access” process. The keyed connectors file defines API-key connectors such as Datadog, including which secret is needed and where it may safely be sent. The Slack manifest declares Slack routes, required secrets, setup tools, and skills for receiving events and sending messages. The sources manifest registers content-source connectors, direct authentication, synced pages, and change notifications. The YC manifest adds YC-specific tools, credentials, onboarding, shared sources, and guidance files. Together, these files let the system discover integrations before anyone uses them.

## [Data schema, migrations, and persistence models](stage-19.md) `stage-19` · (cross-cutting) — 74 files

This stage is the system’s long-term filing cabinet. It is shared behind-the-scenes support used during startup, normal requests, background jobs, and cleanup. It defines what can be stored, how storage changes over time, and how different parts of the product read the same records safely.

At the center, schema/tables.py is the main blueprint for database tables, columns, links, and lookup shortcuts. db.py is the safe doorway that opens database connections, runs transactions for the right workspace, applies migrations, and shuts access down cleanly. The package files simply make the schema and model folders importable.

The sub-stages fill in the cabinet. Control-plane setup creates gateway tables and workspace safety fences. Transcript and record formats preserve conversations and turn state. Core and feature migrations create and evolve tables for workspaces, members, agents, credentials, channels, sources, pages, inbound messages, scheduled tasks, runtimes, grants, billing, exports, seats, memories, search chunks, knowledge graphs, and extension-owned data. Together, they let the codebase upgrade its stored data without losing the history that later work depends on.

### [Control-plane database setup and row isolation](stage-19.1.md) `stage-19.1` — 2 files

This stage is part of startup for the control gateway, the service that manages gateway-specific data before normal requests are served. Its job is to make sure the database is ready and safe to use. It is kept separate from the main application schema because it owns control-gateway tables and the rules for keeping different workspaces apart.

The schema.py file is the table builder and inspector. It prepares the PostgreSQL tables the gateway needs, and checks that they exist in the expected form. This makes setup an intentional step at launch, instead of letting request code quietly create or change tables while the system is already running.

The rls.py file sets up the safety fence. It configures PostgreSQL row-level security, which means the database itself filters rows so one workspace cannot see another workspace’s data. It also creates a limited database role used by the application when handling requests. Together, these files give the gateway a known database shape and a built-in boundary between tenants.

### [Durable transcript and shared record formats](stage-19.2.md) `stage-19.2` — 4 files

This stage is shared behind-the-scenes support. It defines the long-lasting records that let different parts of the system agree on what happened in a conversation, even after a request has moved through queues, workers, billing, memory, or storage.

The transcript files are the conversation’s notebook. core/src/ufo/transcript.py defines the common saved format: how conversation records and shorter “compaction” summaries are named, compressed, written, read, and decoded. core/src/ufo/loop/transcript.py uses that format to safely store and load the active transcript in a shared blob store, which is a place for large saved data. It also prevents an older copy from overwriting newer conversation state.

core/src/ufo/schema/records.py defines the standard shape of a turn, meaning one user request or internal task. It records IDs, status, times, prompts, and results so all system parts speak the same language.

core/src/ufo/subjects.py defines who memory belongs to, either everyone or one member, keeping ownership labels consistent.

### [Core migration runner and initial platform schema](stage-19.3.md) `stage-19.3` — 5 files

This stage is part of setting up and evolving the project’s database. It is the foundation that later application code relies on when it saves workspaces, users, conversations, credentials, and related records. The migration runner, env.py, is the control booth. It configures Alembic, a tool that applies database structure changes step by step, so it can connect to the right database, compare the database with the project’s table definitions, and run upgrades safely.

The first migration, 0001_heartbeat.py, lays the main floor of the system. It creates the earliest tables for workspaces, members, agents, conversations, message turns, and usage costs. The next migrations add rooms to that floor. 0002_credentials.py adds encrypted credential storage for each workspace. 0003_proposal.py adds a place to store proposals. 0004_loop_depth.py updates conversation records so they can represent nested activity, such as a main agent calling a subagent, and expands the allowed conversation types. Together, these files create the first usable platform schema and a path for changing it over time.

### [Surface, channel, and agent-binding migrations](stage-19.4.md) `stage-19.4` — 6 files

This stage is part of the behind-the-scenes database setup that lets the product grow from one conversation place into many. A database migration is a versioned recipe that changes what the database can store. Here, the recipes teach the system which “surfaces” are valid, meaning the places where conversations happen, such as Slack or the web.

The Slack migration adds Slack as a supported surface and creates a delivery-tracking table, so outgoing replies can be recorded, retried, and not lost. The web migration does the same kind of permission work for the web interface, so web conversations and identities are accepted. The surface seam migration removes older fixed limits on surface names and adds storage for files or other shared artifacts attached to a single conversation turn. The workspace keys migration makes delivery records workspace-aware, so two workspaces can use the same surface names without clashing. Finally, the agent migrations add an internet-access setting for each agent and require conversations and surface installations to be linked to the agent that owns them.

### [Source and page persistence migrations](stage-19.5.md) `stage-19.5` — 7 files

This stage is behind-the-scenes setup for the database, the system’s long-term memory. It is not the main work loop. Instead, these migrations change the database shape as the project grows, so synced content can be stored safely across upgrades.

The first migration creates the basic storage: a source table for places content comes from, and a page table for the content found there. Later migrations refine that model. One opens the source backend field so sources are not limited to folders; extensions can add new kinds of sources. Another adds an error streak counter, which lets the system remember repeated failures and back off instead of retrying too aggressively.

Other migrations improve how sources are managed. One adds a removed_at timestamp, so a source can be hidden or retired without erasing its history. Another adds ownership, marking a source as shared or tied to one member. The page-focused migrations add browsing details like stream name, title, and original source timestamps, then rename page timestamp columns so their meaning is clearer.

### [Turn execution, admission, and inbound-message migrations](stage-19.6.md) `stage-19.6` — 11 files

This stage is part of the behind-the-scenes database upgrade path. It does not run the product’s main work itself. Instead, it reshapes the stored data so later code can run turns, admit scheduled work, and receive messages safely.

Several migrations strengthen turn records, which are the saved units of conversation work. One adds fields that show which run attempt is active and prevents duplicate resume jobs. Others store tracing links to a parent turn, surface context such as sender or timezone, the speaker, authorization details, and “on behalf of” member links. These make each turn easier to audit and debug. Another migration adds an index, like a book’s lookup page, so child turns can be found quickly from their parent.

A second group improves admission, meaning how a turn is allowed to start. It records scheduled pauses and allows “scheduled” as a valid admission source.

The inbound-message migrations add a durable queue for incoming messages. Messages can be stored, ordered, deduplicated, consumed into turns, and later cleaned up as the stored display-text design changes.

### [Runtime, grants, scheduling, and fleet migrations](stage-19.7.md) `stage-19.7` — 11 files

This stage is behind-the-scenes database upkeep. It is made of migrations, which are small scripts that change the database shape as the system grows, and can often undo the change during a rollback. Together they prepare storage for runtimes, permissions, scheduled work, and shared fleet processes.

First, 0006 creates ext_store, a per-workspace place where extensions can save small JSON settings or state. 0014 adds permission grants, and 0043 later marks whether a grant is shared. 0015 creates runtime_instance records for live runtime processes; 0028 loosens those records so shared fleet runtimes do not need a workspace, and 0046 removes old fleet columns no longer used. 0017 adds scheduled_task storage for work that should run later or repeatedly. 0037 lets a task remember the last conversation turn it fired on, and 0048 gives tasks an optional expiration time. 0024 lets conversations remember a sandbox handle so they can reconnect to the same durable workspace. 0027 adds indexes, like book tabs, so background sweepers can find jobs and records quickly without reading whole tables.

### [Ledger, spend, export, and seat migrations](stage-19.8.md) `stage-19.8` — 9 files

This stage is shared behind-the-scenes database setup. It changes the stored data rules so later parts of the system can charge usage, limit spending, export records, and manage workspace seats safely.

The spend cap migration adds limits for how much a workspace, member, or agent can spend. It also lets a conversation turn be marked “parked,” meaning paused instead of finished. Several ledger migrations widen what the ledger can record. The ledger is the system’s money-and-usage log. It gains new entry types for egress, meaning data leaving the system, and sandbox token usage. It also gains a price digest field, a small text audit note that helps explain how a charge was priced. Another change lets some ledger rows belong directly to a workspace, not only to a specific turn.

The export migrations add a table that tracks ledger export progress, then add a BYOK flag, meaning whether the export used a customer-provided key. Finally, the seat migrations add workspace membership entitlements: who has taken a seat, optional seat limits, and optional included seats, with checks that these numbers stay positive.

### [Memory, indexing, and knowledge extension migrations](stage-19.9.md) `stage-19.9` — 12 files

This stage is behind-the-scenes database setup for extensions that remember, search, and connect information. These files are migrations, meaning small step-by-step changes to the database structure so stored data can evolve safely over time.

The indexing migrations create storage for searchable text chunks. They add fields for ordinary lookups, full-text search, and vector similarity, which means finding text by meaning as well as exact words. They later add workspace ownership, so each workspace can keep its own copy of a chunk.

The knowledge graph migration builds tables for named entities, like people or companies, and relationships between them, like a map of connected facts.

The memory migrations build the memory system in layers. They first create memory records and memory pages, then add memory type, confidence, and workspace links. Later migrations add indexes, which are database shortcuts, to make cleanup and inventory browsing fast. The final steps add “as-of” time, fill it from page timestamps, and create a clear provenance link showing which page a memory came from.

### [Special-purpose extension migrations](stage-19.10.md) `stage-19.10` — 3 files

This stage is part of the system’s setup and upgrade path. It contains small database migrations, which are step-by-step changes that create or remove tables where extensions keep their data. Each migration belongs to a specific extension, so these features can bring their own storage without changing the core system by hand.

The evaluation environment migration creates the database space for fake email and calendar information. This lets test workspaces have mailbox messages and calendar events that behave like real data, but are meant for controlled evaluation.

The sample extension migration creates a simple table for one text note per workspace. It is a small example of how an extension can store its own workspace-specific information, and it can also remove that table if the change is undone.

The skill creation migration adds a user_skill table. This gives the system a place to save skills created by users, and it can drop that storage during rollback. Together, these migrations prepare extension-owned storage before those extensions run.

## [Public SDK, extension contracts, protocols, and sample conformance](stage-20.md) `stage-20` · (cross-cutting) — 30 files

This stage is the public “plug-in counter” for the system. It is shared behind-the-scenes support, not the main work loop. Its job is to give extension and pack authors stable names to import, so they can add features without depending on private internal files that may move.

One part provides the runtime workbench an extension receives while it runs: a limited context for approved data, credentials, logs, HTTP helpers, model calls, and other safe actions. Another part exposes identity and administration doorways, such as bearer-token checks, credentials, grants, seats, and accounting reports. A third part opens public ports for outside capabilities like browsers, connectors, search indexes, memory, models, objects, sandboxes, sources, and search.

The contribution-contract part defines the manifest, the form an extension fills out to declare tools, jobs, routes, hooks, models, and other offerings. The sample extension proves these contracts work end to end. The direct scheduling and surfaces files add two more stable doorways: one for scheduling tools and one for surface-related extension types and errors.

### [Extension runtime context, package entry, HTTP, and observability SDK](stage-20.1.md) `stage-20.1` — 5 files

This stage is shared runtime support for extensions: small pieces of public “tooling” that extension authors use while their code is running. It sits behind the scenes during request handling or background jobs, giving outside code safe access without exposing the whole system.

The main piece is `ext/context.py`. It creates the limited workbench an extension receives inside a workspace. Through it, code can touch only approved things, such as allowed data, credentials, transcripts, model calls, sources, and proposals. This keeps extensions useful but contained.

The SDK files make that workbench easier and safer to use. `sdk/__init__.py` simply marks the SDK folder as importable Python code. `sdk/context.py` is the stable front door for importing context types, so extension authors do not depend on internal file paths. `sdk/http.py` provides request and response helpers, including safe session cookie setting, without exposing web-framework internals. `sdk/o11y.py` gives a simple logging doorway, so extensions can record messages and warnings in the project’s structured log format.

### [SDK identity, authorization, credentials, and administration doorways](stage-20.2.md) `stage-20.2` — 8 files

This stage is shared behind-the-scenes support for people who build extensions or outside tools on top of the system. It is not where authentication or accounting is invented. Instead, these files act like labeled front doors in the SDK, the public toolkit that other code is meant to import from. They hide the deeper internal layout so that outside code can stay stable even if the project is reorganized.

The accounting doorway exposes spend reports and usage totals. Authproxy gathers the pieces needed to plug in an authentication backend. Bearer provides safe access to bearer-token checks, where a bearer token is a digital pass presented with a request. Credentials exposes approved tools for working with stored access information. Grants opens access to grant audit summaries, showing who received what permissions. Hub republishes hub-related types used for connecting to central services. Operator exposes helpers for operator-only web sessions. Seats provides the public rules and data types for seat policy. Together, these modules form a controlled reception desk for identity, permission, and administration features.

### [SDK external capability and data protocol doorways](stage-20.3.md) `stage-20.3` — 9 files

This stage is shared support for people building extensions around UFO. It is not the main work loop itself. Instead, it provides “doorways”: stable public files that extension code can import from, instead of reaching into UFO’s private internal folders that may change.

Each file opens a doorway to one kind of outside capability or data shape. browser.py defines the public way to talk about browser sessions. connectors.py gathers connector service and OAuth sign-in pieces for linking outside services. index.py exposes tools for search indexes and text embeddings, which turn text into searchable representations. memory.py provides the approved memory-search types. models.py collects model-facing pieces such as messages, model clients, pricing or capability descriptions, and helper functions. objects.py exposes object types and storage interfaces. sandbox.py gathers tools for working with sandboxed execution areas, meaning controlled places where code or data can be carried safely. search.py provides the standard search interface. sources.py collects building blocks for source-sync extensions. Together, these files act like labeled ports on a machine, keeping extension integrations clean and stable.

### [SDK contribution contracts and sample extension conformance](stage-20.4.md) `stage-20.4` — 6 files

This stage is shared support for extension authors. An extension is add-on code that plugs new abilities into UFO without changing the core system. The main contract lives in manifest.py. It defines the “menu form” an extension fills out to say what it offers, such as tools, web routes, jobs, credentials, connectors, hooks, skills, models, search, and sandbox support.

The SDK files are the public doorways into that contract. sdk/manifest.py exposes the manifest types from one stable place. sdk/tools.py, sdk/jobs.py, and sdk/skills.py do the same for tools, background jobs, and skills. They mostly forward names from deeper code, so extension authors do not need to know the core’s internal folder layout.

The sample extension, ufo_ext_sample.py, is the proving ground. It declares many example contributions using these public SDK paths. Tests can load it like a real extension and check that the whole path works, from declaration to runtime behavior.

## [Observability, accounting, live infrastructure, and operator diagnostics](stage-21.md) `stage-21` · (cross-cutting) — 8 files

This stage is the system’s control room. It is shared behind-the-scenes infrastructure used during startup, normal requests, background jobs, and shutdown. It helps operators see what is happening, understand costs, and inspect live data safely.

The observability file sets up traces, metrics, and structured logs. In plain terms, it leaves organized breadcrumbs about what the system is doing, while hiding sensitive values before they are sent out. The accounting file is the bookkeeper. It records token use, network use, spending limits, billing changes, and summaries. The pricing file supplies the price list used to turn token counts into money, and fingerprints that list so old bills can be checked later.

Operator access is handled by a shared helper that checks bearer-token login, stores it in a secure cookie, and limits each request to the right workspace. The debugger extension then uses those rules to serve read-only pages and endpoints for conversations, turns, files, transcripts, compactions, and live streams. The memory extension adds a similar read-only view of stored memories. The small package files simply make the debugger and Redis hub extensions importable.
