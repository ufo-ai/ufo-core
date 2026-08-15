# Prompt self-improvement and governed prompt changes  `stage-18.3`

This stage is behind-the-scenes maintenance, not part of the live chat loop. Its job is to learn from past agent mistakes and suggest safer, better system prompts, which are the standing instructions that guide an agent’s behavior. It does this offline so it does not disturb users or change the outside world.

The self-improvement extension defines this review process. The corpus builder collects old conversations where tools failed, groups them by the failing tool, and keeps some examples for learning and some for testing. The model adapter gives the extension a simple way to ask a language model for suggestions or judged answers. The proposer uses the failure examples to draft a new prompt, but only if it is truly different.

The replay code reruns saved tasks with old tool results fixed, like testing a new driver on a closed track. Evaluation compares the old and new prompts, while the gate uses cautious statistics to reject weak or risky changes. The cron job runs this regularly. If a change keeps passing, governance opens a human-approved proposal and applies it only if the original prompt has not changed meanwhile.

## Files in this stage

### Governed extension boundary
Defines the self-improvement extension scope and the proposal-based guardrail for applying prompt changes safely.

### `core/src/ufo/governance.py`

`domain_logic` · `request handling`

This file is a safety gate for changing an agent’s prompt. Instead of letting code overwrite an agent’s instructions immediately, it creates a proposal that says, “change this prompt from version A to version B.” The “version” is a digest: a short fingerprint made from the prompt text. Like checking a seal on an envelope, the system can later tell whether the prompt has changed without comparing long text by hand.

The main idea is compare-and-swap: approve the change only if the current prompt still matches the old fingerprint recorded in the proposal. This prevents accidentally approving an outdated change after someone else has already edited the agent. If the fingerprint no longer matches, the proposal is rejected rather than applied.

The Governance class is tied to one workspace, so proposals cannot cross workspace boundaries. It first verifies that the target agent exists in that workspace, then stores the proposed new prompt and its fingerprints in the database. Later, approval re-reads the proposal, locks the agent row while checking it, and either updates the prompt and marks the proposal approved, or marks it rejected and logs what happened. Without this file, prompt changes would be much easier to apply stale, silently overwrite newer work, or affect the wrong workspace.

#### Function details

##### `prompt_digest`  (lines 16–17)

```
def prompt_digest(prompt: str) -> str
```

**Purpose**: This makes a stable fingerprint for a prompt string. The fingerprint is used to tell whether a prompt is still the same as it was when a proposal was created.

**Data flow**: It takes prompt text as input, turns that text into bytes, runs it through SHA-256, which is a standard one-way fingerprinting method, and returns the fingerprint as a text string. It does not change anything outside itself.

**Call relations**: When a change is proposed, Governance.propose_change uses this to record the fingerprint of the proposed new prompt. When a proposal is approved, Governance.approve_proposal uses it again to check whether the agent’s current prompt still matches the proposal’s expected starting point.

*Call graph*: called by 2 (approve_proposal, propose_change); 1 external calls (sha256).


##### `Governance.propose_change`  (lines 28–55)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: This opens a new proposal to change an agent’s prompt. It does not change the agent yet; it only records the requested change so it can be reviewed and safely approved later.

**Data flow**: It receives an AgentChange, which includes the target agent, the expected current prompt fingerprint, and the new prompt text. It creates a new proposal ID, opens a workspace database transaction, checks that the agent exists in this workspace, stores the proposal with the old fingerprint, the new prompt, and the new prompt’s fingerprint, then returns a ProposalRef pointing to the created proposal. If the agent is not found in the workspace, it raises an error instead of creating anything.

**Call relations**: This is the first half of the governance flow. Callers use it when they want to request a prompt change. It relies on prompt_digest to fingerprint the new prompt, uses the workspace transaction helper to make the database write safely, and returns a reference that can later be passed to Governance.approve_proposal.

*Call graph*: calls 1 internal fn (prompt_digest); 5 external calls (__init__, insert, select, workspace_tx, uuid4).


##### `Governance.approve_proposal`  (lines 57–114)

```
async def approve_proposal(self, proposal_id: UUID) -> None
```

**Purpose**: This tries to apply a pending prompt-change proposal. It only updates the agent if the agent’s prompt has not changed since the proposal was made.

**Data flow**: It receives a proposal ID and opens a workspace database transaction. It reads the proposal for this workspace, rejects missing or non-pending proposals with an error, then reads and locks the target agent’s current prompt so no competing update can slip in during the check. It fingerprints the current prompt and compares it with the proposal’s expected old fingerprint. If they differ, it marks the proposal rejected and logs that rejection. If they match, it writes the new prompt to the agent, marks the proposal approved, and logs the approval after the transaction completes.

**Call relations**: This is the second half of the governance flow, normally called after a proposal has been reviewed and chosen for approval. It uses prompt_digest to detect stale proposals, database updates to either apply or reject the change, and the logging helper to leave an observable record of the final outcome.

*Call graph*: calls 1 internal fn (prompt_digest); 4 external calls (select, update, workspace_tx, log).


### `extensions/self_improvement/ufo_ext_self_improvement/__init__.py`

`other` · `package import`

This package initializer is very small, but it gives an important signpost for the rest of the extension. It explains that the self-improvement extension runs an offline replay evaluation loop. In plain terms, that means it looks back at recorded work sessions or “trajectories” from the workspace, replays or studies them, and uses what it learns to suggest improvements. Those improvements are not applied automatically. Instead, the extension opens governed prompt changes, meaning proposed edits to the system’s prompts that follow an approval process. A human member must review and approve them before they become real changes. Without this package file, Python would not treat this folder as an importable package in the usual way, and readers would also lose the simplest summary of the extension’s purpose. Think of it like a label on a toolbox: it does not do the repair itself, but it tells you this toolbox is for learning from past jobs and carefully proposing better instructions for next time.


### Corpus and prompt candidates
Builds failure-focused evaluation sets and uses a model adapter to propose meaningful replacement prompts.

### `extensions/self_improvement/ufo_ext_self_improvement/corpus.py`

`domain_logic` · `self-improvement corpus building before proposal and evaluation`

The self-improvement system needs real examples of where it struggled. This file finds those examples in recorded conversation histories, called trajectories. Since the system does not have a separate “I had trouble here” signal, it uses tool errors as the clue: if a tool call failed during a conversation, that conversation may show something worth improving.

The file reduces each flagged trajectory into a TaskExample. That example keeps the conversation id, the user’s original request, the full message history needed to replay or inspect the situation, and a plain description of the problem, such as “the search tool errored: ...”. It then groups these examples into TaskClass objects. Each class represents one kind of failure, named after the tool that failed, for example “tool:browser”.

A key detail is the split between “mine” and “held_out”. The mine set is what the improvement proposer can learn from. The held-out set is kept separate so a proposed improvement can be tested on examples it did not directly learn from. This is like studying with practice questions, then taking a quiz made from different questions. Without this separation, the system could appear to improve simply by fitting the exact examples it saw.

#### Function details

##### `first_request`  (lines 39–43)

```
def first_request(messages: tuple[Message, ...]) -> str | None
```

**Purpose**: Finds the first real user request in a conversation. This gives the self-improvement loop a clear task statement to grade against later.

**Data flow**: It receives the full list of messages from a trajectory. It scans them in order until it finds a message from the user whose content is plain text and not empty. It returns that text, or returns nothing if no suitable user request exists.

**Call relations**: When bad_trajectory is deciding whether a conversation can become a training or evaluation example, it calls first_request to make sure there is an original user request. If this function cannot find one, that trajectory is skipped because there is no clear task to judge.

*Call graph*: called by 1 (bad_trajectory).


##### `first_tool_error`  (lines 46–65)

```
def first_tool_error(messages: tuple[Message, ...]) -> tuple[str, str] | None
```

**Purpose**: Finds the first failed tool call in a conversation and reports which tool failed and what error text it produced. This is the main signal used to decide that a trajectory is worth studying.

**Data flow**: It receives the full message history. First it notes which tool-use ids belong to which tool names. Then it scans again for the first tool result marked as an error, matches it back to the tool that produced it, and returns the tool name plus the error message. If no matching failed tool result is found, it returns nothing.

**Call relations**: bad_trajectory calls this function before building a TaskExample. The result tells bad_trajectory both whether the trajectory is useful and which task class it belongs to, because examples are grouped by the tool that failed.

*Call graph*: called by 1 (bad_trajectory).


##### `bad_trajectory`  (lines 68–78)

```
def bad_trajectory(trajectory: Trajectory) -> tuple[str, TaskExample] | None
```

**Purpose**: Turns one trajectory into a flagged example if it contains both a user request and a tool error. It also chooses the class name for that example based on the failing tool.

**Data flow**: It receives one trajectory, including its conversation id and messages. It asks first_tool_error for the first tool failure and first_request for the first user request. If either is missing, it returns nothing. Otherwise it creates a TaskExample containing the conversation id, request, full messages, and a human-readable problem description, then returns it together with a class name like “tool:toolname”.

**Call relations**: task_classes calls bad_trajectory for every trajectory it is given. bad_trajectory is the filter between raw conversation history and the cleaner corpus format: it uses first_tool_error and first_request to decide whether each trajectory is useful, then hands back a ready-to-group example.

*Call graph*: calls 2 internal fn (first_request, first_tool_error); called by 1 (task_classes); 1 external calls (__init__).


##### `task_classes`  (lines 81–94)

```
def task_classes(trajectories: tuple[Trajectory, ...]) -> tuple[TaskClass, ...]
```

**Purpose**: Builds the final set of task classes from many trajectories. It groups failed conversations by the tool that errored, removes groups that are too small, splits the rest into learning and held-out examples, and returns them in a stable order.

**Data flow**: It receives a tuple of trajectories. For each one, it asks bad_trajectory whether the trajectory contains a usable tool failure. Usable examples are collected under their class name. Each group is then passed to _split, which may return a TaskClass or reject the group if it is too small. The function returns the accepted TaskClass objects, sorted so larger classes come first and ties are ordered by name.

**Call relations**: This is the main public builder in the file. It drives the whole corpus-making flow by repeatedly calling bad_trajectory to extract examples and _split to divide each class into mine and held-out sets.

*Call graph*: calls 2 internal fn (_split, bad_trajectory).


##### `_split`  (lines 97–102)

```
def _split(name: str, examples: tuple[TaskExample, ...]) -> TaskClass | None
```

**Purpose**: Divides one group of examples into a mine set and a held-out set. It refuses to create a class unless there are enough examples to have at least one for learning and one for evaluation.

**Data flow**: It receives a class name and all examples for that class. If there are too few examples, it returns nothing. Otherwise it sorts the examples by conversation id for a repeatable split, chooses how many should be held out, and returns a TaskClass with held-out examples first in the sorted order and mine examples after them.

**Call relations**: task_classes calls _split after collecting examples by failing tool. _split is the gatekeeper that prevents tiny classes from entering the corpus and enforces the important separation between examples used to propose an improvement and examples used to test it.

*Call graph*: called by 1 (task_classes); 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/model.py`

`io_transport` · `cross-cutting during self-improvement model calls`

The self-improvement extension needs to use a language model in a few different places, such as proposing changes, replaying behavior, and grading results. Without this file, each part of the extension would have to know the details of how the SDK expects model requests to be built, which would spread repeated setup code and make the extension harder to keep consistent.

The file defines two small promises, called protocols: `ModelLeg` is anything that can take a system instruction plus chat messages and return plain text, while `ReplayLeg` is anything that can take the same conversation plus available tools and return a full model message. A protocol is like saying, “anything with these methods can be used here,” even if it is a different concrete class.

`ModelAccessLeg` is the real adapter. It wraps the SDK’s `ModelAccess`, which is the project’s metered doorway to a model. “Metered” means usage can be tracked or limited. When code asks it to complete or take a turn, it builds a `ModelRequest` with the selected model, the given conversation, a shared maximum output size, and reasoning turned off. The result is a single, consistent model access point for the whole extension.

#### Function details

##### `ModelLeg.complete`  (lines 13–13)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This defines the shape of a simple text-completion model call. Any object that follows this promise can be used where the extension only needs a final text answer from the model.

**Data flow**: It is given a system instruction and a tuple of chat messages. An implementation is expected to send those to a model and return the model’s response as a string.

**Call relations**: This is a protocol method, so it describes what other code may call rather than doing the work itself. `ModelAccessLeg.complete` is the concrete version in this file that fulfills this promise using the SDK model access layer.


##### `ReplayLeg.turn`  (lines 17–19)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This defines the shape of a model call that can also see tool definitions and return a full chat message. It is useful for replay-style flows where the model may need to choose or describe a tool action, not just produce plain text.

**Data flow**: It is given a system instruction, prior chat messages, and a tuple of tool schemas, which describe tools the model may use. An implementation is expected to return a `Message`, meaning a structured chat response rather than just a string.

**Call relations**: This protocol sets the contract for replay-capable model callers. `ModelAccessLeg.turn` is the concrete implementation here, turning that simple contract into an SDK `ModelRequest`.


##### `ModelAccessLeg.complete`  (lines 28–37)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This sends a plain text request to the underlying SDK model service. It is used when the extension wants the model to answer with text only, using the extension’s standard limits and settings.

**Data flow**: It receives a system instruction and previous chat messages. It wraps them into a `ModelRequest`, adding the chosen model name, a maximum output limit of 2048 tokens, and `reasoning` set to off. It sends that request through `self.model.complete` and returns the resulting text string.

**Call relations**: This is the concrete worker behind the `ModelLeg.complete` promise. When higher-level self-improvement code needs a text answer, it can call this narrow adapter instead of building SDK requests itself; this function then creates the `ModelRequest` and hands it to the SDK’s metered model access object.

*Call graph*: 1 external calls (__init__).


##### `ModelAccessLeg.turn`  (lines 39–51)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This sends a tool-aware chat turn request to the underlying SDK model service. It is used when the extension needs a structured model message, possibly involving available tools.

**Data flow**: It receives a system instruction, prior chat messages, and tool schemas. It packages them into a `ModelRequest`, including the selected model, the shared output-token limit, the available tools, and `reasoning` set to off. It sends the request through `self.model.turn` and returns the resulting `Message`.

**Call relations**: This is the concrete worker behind the `ReplayLeg.turn` promise. Replay or grading-style code can call it with the conversation and tool list, and this function translates that simple request into the SDK’s expected request object before passing it to the metered model access layer.

*Call graph*: 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/proposer.py`

`domain_logic` · `self-improvement proposal phase`

This file is one step in a self-improvement loop. Imagine an agent has a written instruction sheet, called a system prompt, and the project has collected examples where that instruction sheet did not guide the agent well enough. This file prepares those examples, shows them to another model, and asks for a careful rewrite of the instruction sheet.

The central idea is conservative improvement. The model is not asked to reinvent the agent or specialize it for just one problem. The system instruction tells it to preserve the agent’s general purpose and voice, and to make the smallest useful change. That matters because a prompt that fixes one task but damages many others would be a bad trade.

The file defines a small result object, `PromptCandidate`, which stores the task class name and the proposed prompt text. `PromptProposer` is the main worker. It takes the current prompt and a `TaskClass`, which contains mined examples of requests and problems. If there are no examples, it gives up immediately. Otherwise it builds a clear request for the model, sends it through a `ModelLeg` abstraction, cleans up common formatting noise like Markdown code fences, and rejects empty or unchanged answers. The output is either a usable candidate prompt or nothing.

#### Function details

##### `PromptProposer.propose`  (lines 33–43)

```
async def propose(self, current_prompt: str, task_class: TaskClass) -> PromptCandidate | None
```

**Purpose**: This is the main action in the file: it tries to produce a revised system prompt for a task area where the agent has had trouble. Someone would use it when they have the current prompt plus examples of failures and want a candidate improvement to review or test.

**Data flow**: It receives the current system prompt and a task class with mined problem examples. If the task class has no examples, it returns nothing. Otherwise it builds a prompt for the model, sends that prompt along with the fixed proposer instructions, receives text back, cleans that text, and checks whether it is empty or the same as the original prompt. If the response looks useful, it returns a `PromptCandidate` containing the task class name and the proposed new prompt.

**Call relations**: This method is the coordinator for the proposer. It calls `PromptProposer._prompt` to turn the current prompt and examples into a model-readable request, wraps that request in a `Message`, asks the configured model to complete it, then calls `_clean` before deciding whether to create a `PromptCandidate`.

*Call graph*: calls 2 internal fn (_prompt, _clean); 2 external calls (__init__, __init__).


##### `PromptProposer._prompt`  (lines 45–56)

```
def _prompt(self, current_prompt: str, task_class: TaskClass) -> str
```

**Purpose**: This helper writes the user-facing request that will be sent to the model. Its job is to present the task class, the current system prompt, and a limited set of failure examples in a clear format.

**Data flow**: It receives the current prompt and a task class. It takes only up to the allowed number of examples, trims each request and problem to the allowed character length, labels them as numbered examples, and places them under headings. It returns one formatted text block that asks for the full revised system prompt.

**Call relations**: It is used by `PromptProposer.propose` just before the model call. In the larger flow, it acts like the briefing writer: `propose` gathers the materials, `_prompt` turns them into a concise briefing, and the model uses that briefing to draft a replacement prompt.

*Call graph*: called by 1 (propose).


##### `_clean`  (lines 59–68)

```
def _clean(text: str) -> str
```

**Purpose**: This helper removes simple formatting wrappers from the model’s answer so the rest of the system can compare and use the actual prompt text. It especially accounts for models that return the prompt inside Markdown code fences, even though they were asked not to.

**Data flow**: It receives raw text from the model. It trims leading and trailing whitespace. If the text starts with a triple-backtick code fence, it removes the opening fence and a closing fence if present, then trims the result again. It returns the cleaned prompt body as plain text.

**Call relations**: It is called by `PromptProposer.propose` after the model responds and before the no-op checks happen. This matters because a prompt wrapped in code fences might otherwise look different from the current prompt or be awkward to store, even though the useful content is inside.

*Call graph*: called by 1 (propose).


### Replay evaluation gates
Replays saved tasks without side effects, judges prompt outcomes, and applies cautious statistical acceptance gates.

### `extensions/self_improvement/ufo_ext_self_improvement/evaluation.py`

`domain_logic` · `candidate evaluation before accepting a prompt change`

This file is the evidence-gathering step for prompt self-improvement. A candidate prompt should not be accepted just because it sounds better. It must prove itself on saved examples. The file compares two “arms”: the current prompt, called absent, and the candidate prompt, called present. Both are tested on the same archived tasks, using the same replay setup and the same judge, so the prompt text is the main thing being compared.

The central class, CandidateEvaluation, takes two model-like helpers. One replays the agent’s behavior on old task messages. The other acts as a grader. For each held-out task, the class runs the replay twice: once with the old prompt and once with the candidate. Then it asks the judge whether the final answer satisfies the original request. Each result becomes an OutcomeLabel, which records whether the candidate prompt was present and whether the answer succeeded.

There are two groups of examples. The local group tests the kind of task the candidate is meant to improve. The global group checks that the candidate does not make other kinds of tasks worse. The collected labels are passed to two_stage_gate, which makes the final verdict. In plain terms, this file is like a fair taste test: same dish, same judges, only one ingredient changed.

#### Function details

##### `CandidateEvaluation.evaluate`  (lines 24–33)

```
async def evaluate(self, candidate_prompt: str, current_prompt: str, local_held_out: tuple[TaskExample, ...], global_held_out: tuple[TaskExample, ...]=()) -> GateVerdict
```

**Purpose**: This is the main entry point for judging a candidate prompt. It compares the candidate prompt with the current prompt on both local held-out examples and optional global held-out examples, then asks the gate whether the evidence is strong enough to accept the candidate.

**Data flow**: It receives the candidate prompt, the current prompt, a set of local saved task examples, and optionally a set of global saved task examples. It turns each example set into success/failure labels by calling _labels, then passes the local and global label groups into two_stage_gate. The result is a GateVerdict, which says whether the candidate passed the evaluation rule.

**Call relations**: This method drives the evaluation flow. It calls _labels twice: first to measure whether the candidate improves the target task area, and then to check broader tasks for regressions. After that, it hands the evidence to two_stage_gate, which makes the final accept-or-reject decision.

*Call graph*: calls 1 internal fn (_labels); 1 external calls (two_stage_gate).


##### `CandidateEvaluation._labels`  (lines 35–45)

```
async def _labels(self, candidate_prompt: str, current_prompt: str, held_out: tuple[TaskExample, ...]) -> tuple[OutcomeLabel, ...]
```

**Purpose**: This helper turns a group of saved tasks into comparison labels. For every task, it tests both the current prompt and the candidate prompt, then records whether each run produced an acceptable answer.

**Data flow**: It receives both prompt texts and a tuple of saved TaskExample objects. It creates a ReplayEvaluation using the configured replay model and round limit. For each example, it replays the old prompt and the candidate prompt against the same saved messages, sends the final answer to _accepts for grading, and stores an OutcomeLabel saying which prompt arm was used and whether it succeeded. It returns all labels as a tuple.

**Call relations**: This method is called by evaluate when evidence is needed for either the local or global example set. Inside the loop, it uses ReplayEvaluation to regenerate answers and calls _accepts to judge each answer. It packages each judged run as an OutcomeLabel for the later gate decision.

*Call graph*: calls 1 internal fn (_accepts); called by 1 (evaluate); 2 external calls (__init__, __init__).


##### `CandidateEvaluation._accepts`  (lines 47–59)

```
async def _accepts(self, request: str, answer: str) -> bool
```

**Purpose**: This helper asks the judge model whether one answer satisfies one user request. It expects the judge to return a small JSON object and treats unclear or malformed replies as rejection.

**Data flow**: It receives the original request and the answer produced during replay. It builds a user message containing both, sends that message with the grading instructions to the judge model, and searches the judge’s text for a JSON object. If the JSON can be parsed and contains "accepted": true, it returns true. If the judge gives no valid JSON, invalid JSON, or anything other than true, it returns false.

**Call relations**: This method is called by _labels after each replayed answer is produced. It creates the Message sent to the judge and uses json.loads to read the judge’s JSON response. Its boolean result becomes the success value inside an OutcomeLabel.

*Call graph*: called by 1 (_labels); 2 external calls (__init__, loads).


### `extensions/self_improvement/ufo_ext_self_improvement/gate.py`

`domain_logic` · `self-improvement evaluation`

This file is the promotion gate for self-improvement. Imagine testing a new recipe against the old one: it is not enough for the new recipe to win a few tastings by luck. This gate asks, “Do we have enough samples, and is the improvement still visible after allowing for uncertainty?”

Each replayed example is recorded as an OutcomeLabel: whether it used the candidate prompt, and whether the judge accepted the result. The file turns those labels into a Contingency table, which simply counts wins and totals for the candidate side and the current-prompt side.

The main local check compares acceptance rates: how often the candidate was accepted minus how often the old prompt was accepted. Instead of trusting the raw difference, it computes a lower confidence bound, meaning a cautious estimate of the improvement after accounting for small sample sizes. The candidate must clear a minimum improvement floor, and both sides must have enough examples.

There is also a global safety check. A prompt may improve one task type while hurting others. The global check only blocks when the evidence strongly says the candidate is worse on the other task classes. If there is too little evidence, it does not block. The final two-stage gate combines these rules: win locally, and do not clearly regress globally.

#### Function details

##### `wilson_lower_bound`  (lines 53–60)

```
def wilson_lower_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: This calculates a cautious lower estimate for a success rate. Someone uses it when they know how many attempts succeeded and want to avoid over-trusting a small sample.

**Data flow**: It receives a count of accepted examples, a total number of examples, and an optional confidence setting. If there are no examples, it returns 0. Otherwise it computes the Wilson lower bound, using square-root math to account for uncertainty, and returns a number between 0 and the observed success rate.

**Call relations**: The lift calculations call this when they need the pessimistic side of an acceptance rate. It supplies one piece of the uncertainty range used by both the local improvement check and the global safety check.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `wilson_upper_bound`  (lines 63–70)

```
def wilson_upper_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: This calculates a cautious upper estimate for a success rate. It is used when the code needs to know how high a rate could reasonably be, given the sample size.

**Data flow**: It receives accepted examples, total examples, and an optional confidence setting. With no examples, it returns 1, meaning the rate is completely uncertain. Otherwise it computes the Wilson upper bound and returns a number between the observed success rate and 1.

**Call relations**: The lift calculations call this alongside the lower-bound function. Together, those two functions describe how uncertain each side of the prompt comparison is.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `lift_lower_bound`  (lines 73–84)

```
def lift_lower_bound(cont: Contingency) -> float
```

**Purpose**: This estimates the worst believable improvement of the candidate prompt over the old prompt. It answers, “Even after being cautious, does the candidate still look better?”

**Data flow**: It receives a Contingency table with accepted counts and totals for candidate-present and candidate-absent examples. It computes each side’s acceptance rate, asks for the candidate’s lower uncertainty bound and the old prompt’s upper uncertainty bound, then combines those uncertainties into one cautious lower bound for the difference. If either side has no examples, it returns 0.

**Call relations**: score_gate calls this after building the counts from labels. This function relies on wilson_lower_bound and wilson_upper_bound to avoid mistaking random noise for a real lift.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (score_gate); 1 external calls (sqrt).


##### `lift_upper_bound`  (lines 87–98)

```
def lift_upper_bound(cont: Contingency) -> float
```

**Purpose**: This estimates the best believable improvement of the candidate prompt over the old prompt. It is mainly used to detect whether a candidate is clearly harmful on broader tasks.

**Data flow**: It receives a Contingency table. It computes the observed difference in acceptance rates, then adds an uncertainty allowance based on the candidate’s upper bound and the old prompt’s lower bound. If either side has no examples, it returns 0.

**Call relations**: global_non_inferior calls this during the global safety check. If even this optimistic estimate is still meaningfully negative, the candidate is treated as a real regression.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (global_non_inferior); 1 external calls (sqrt).


##### `contingency`  (lines 101–109)

```
def contingency(labels: tuple[OutcomeLabel, ...]) -> Contingency
```

**Purpose**: This turns individual replay results into the four counts needed for comparison. It separates examples where the candidate prompt was present from examples where it was absent.

**Data flow**: It receives a tuple of OutcomeLabel records. It splits them into candidate-present and candidate-absent groups, counts how many in each group succeeded, counts the group sizes, and returns a Contingency object with those four numbers.

**Call relations**: score_gate and global_non_inferior both call this before doing statistics. It is the counting step that feeds the later confidence-bound calculations.

*Call graph*: called by 2 (global_non_inferior, score_gate); 1 external calls (__init__).


##### `score_gate`  (lines 112–139)

```
def score_gate(labels: tuple[OutcomeLabel, ...], lower_bound: float=LIFT_LOWER_BOUND, n_floor: int=N_FLOOR) -> GateVerdict
```

**Purpose**: This gives the local verdict for the task class the candidate was meant to improve. It passes only when there are enough replays on both sides and the cautious improvement estimate clears the required floor.

**Data flow**: It receives replay labels plus optional thresholds for the required lift and minimum examples per side. It counts the results with contingency, computes the lower lift estimate with lift_lower_bound, then returns a GateVerdict explaining pass or fail, the measured lower bound, and the sample counts.

**Call relations**: two_stage_gate calls this first. If this local gate fails, the full process stops there and returns the same failure verdict without checking global performance.

*Call graph*: calls 2 internal fn (contingency, lift_lower_bound); called by 1 (two_stage_gate); 1 external calls (__init__).


##### `global_non_inferior`  (lines 142–154)

```
def global_non_inferior(labels: tuple[OutcomeLabel, ...], margin: float=GLOBAL_REGRESSION_MARGIN, n_floor: int=N_FLOOR) -> bool
```

**Purpose**: This checks whether the candidate avoids clearly hurting other task classes. It is intentionally forgiving when evidence is thin, but blocks when the statistics show a meaningful regression.

**Data flow**: It receives replay labels for the broader held-out task set, plus optional margin and sample-size settings. It counts candidate-present and candidate-absent results. If either side has too few examples, it returns true. Otherwise it computes the upper lift bound and returns true unless even that optimistic bound is below the allowed negative margin.

**Call relations**: two_stage_gate calls this only after the local score gate has passed. It uses contingency and lift_upper_bound to decide whether the local win is safe enough globally.

*Call graph*: calls 2 internal fn (contingency, lift_upper_bound); called by 1 (two_stage_gate).


##### `two_stage_gate`  (lines 157–174)

```
def two_stage_gate(local_labels: tuple[OutcomeLabel, ...], global_labels: tuple[OutcomeLabel, ...]) -> GateVerdict
```

**Purpose**: This is the full promotion decision for a candidate prompt. It requires both a proven local improvement and no confident global regression.

**Data flow**: It receives one set of labels for the local task class and another set for global held-out tasks. It first runs score_gate on the local labels. If that fails, it returns the local failure verdict. If local passes, it runs global_non_inferior; a global regression creates a new failing GateVerdict, otherwise the original passing local verdict is returned.

**Call relations**: This function ties the file’s two checks together. It hands the local decision to score_gate, then asks global_non_inferior for the safety check before allowing a candidate to promote.

*Call graph*: calls 2 internal fn (global_non_inferior, score_gate); 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/replay.py`

`domain_logic` · `evaluation/replay run`

This file solves a careful testing problem: how can the system compare two prompt versions fairly when the original task may have used tools, such as searches or file actions? Instead of letting the model call live tools again, it replays the old conversation like a recorded game tape. The model is allowed to ask for tools, but every tool answer is copied from the archived run.

The flow starts by removing the original final assistant answer, leaving the earlier user messages, tool requests, and tool results as context. It then builds a small tool catalog from only the tools that appeared in that archive. This means the replay knows just enough for the model to repeat the same kinds of calls, but it cannot reach into the live agent’s full tool system.

During replay, the model runs one turn at a time under the candidate system prompt. If it gives a final text answer, the replay succeeds. If it asks for a tool, the code looks up the matching archived result using the tool name and a normalized version of the tool input. If no matching result exists, the model has wandered off the recorded path; the replay stops and marks the result as diverged. A round limit prevents endless loops. The result records the final or partial text, whether the replay diverged, and how many model turns were used.

#### Function details

##### `_canonical_input`  (lines 35–36)

```
def _canonical_input(value: object) -> str
```

**Purpose**: This helper turns a tool’s input data into a stable text key. It exists so the same tool call can be recognized even if ordinary dictionary ordering would otherwise make two equal inputs look different.

**Data flow**: It receives any input value from a tool call. It converts that value to compact JSON text with keys sorted in a predictable order. It returns that text, which can then be used as part of a lookup key.

**Call relations**: When archived tool results are indexed, archived_tool_results uses this helper to store each result under a stable tool-input key. Later, _feed_archived uses the same helper on newly replayed tool calls so it can find the matching archived result.

*Call graph*: called by 2 (_feed_archived, archived_tool_results); 1 external calls (dumps).


##### `replay_head`  (lines 39–52)

```
def replay_head(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This function prepares the archived conversation for replay by removing the old final answer. It keeps the setup and tool history, because those are the facts the new prompt should work from.

**Data flow**: It receives the full archived message history. Starting from the end, it removes trailing assistant messages that are plain final answers and do not contain tool calls. It returns the shortened message tuple that the replay model will see as its starting context.

**Call relations**: ReplayEvaluation.replay calls this near the start of a replay. The returned conversation head becomes the base history that each new model turn is added to.

*Call graph*: called by 1 (replay).


##### `archived_tool_results`  (lines 55–77)

```
def archived_tool_results(messages: tuple[Message, ...]) -> dict[tuple[str, str], ToolResultBlock]
```

**Purpose**: This function builds a lookup table of old tool answers. It lets the replay answer tool calls from the archive instead of executing real tools again.

**Data flow**: It receives the archived messages. First it collects tool result blocks by their original tool-use id. Then it walks through the archived tool-use blocks and pairs each tool name plus normalized input with the result that answered it. It returns a dictionary mapping each recorded call shape to its archived result.

**Call relations**: ReplayEvaluation.replay calls this before asking the model to replay the task. Later, _feed_archived uses the resulting lookup table to answer any tool calls the model makes during replay.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay).


##### `replay_tools`  (lines 80–97)

```
def replay_tools(messages: tuple[Message, ...]) -> tuple[ToolSchema, ...]
```

**Purpose**: This function creates the limited list of tools the replay model is allowed to know about. The list comes only from tools that were actually used in the archived conversation.

**Data flow**: It receives the archived messages and scans them for tool-use blocks. For each distinct tool name, in first-seen order, it creates a permissive tool schema, which is a simple description saying the tool accepts an object-shaped input. It returns those schemas as a tuple.

**Call relations**: ReplayEvaluation.replay calls this during setup and passes the returned tool schemas into each model turn. This gives the model enough tool information to reproduce archived calls without connecting it to the live tool registry.

*Call graph*: called by 1 (replay); 1 external calls (__init__).


##### `_feed_archived`  (lines 100–116)

```
def _feed_archived(tool_uses: tuple[ToolUseBlock, ...], results: Mapping[tuple[str, str], ToolResultBlock]) -> Message | None
```

**Purpose**: This function answers a replayed round of tool calls using the archived results. If any requested call was not seen in the archive, it reports that replay cannot continue on the same path.

**Data flow**: It receives the model’s requested tool calls and the archived-result lookup table. For each requested call, it normalizes the input and searches for the matching archived result. If every call matches, it creates a new user message containing tool result blocks with the new call ids but the old result contents. If any call has no match, it returns None.

**Call relations**: ReplayEvaluation.replay calls this whenever the replayed model asks for tools. A returned message is added to the conversation so the model can continue; None tells ReplayEvaluation.replay that the model diverged from the archived tool path.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay); 2 external calls (__init__, __init__).


##### `ReplayEvaluation.replay`  (lines 129–150)

```
async def replay(self, archived: tuple[Message, ...], system_prompt: str) -> ReplayResult
```

**Purpose**: This is the main replay procedure for testing one archived task against one candidate system prompt. It runs only the model parts again, while reusing old tool results, so the prompt can be judged without side effects.

**Data flow**: It receives an archived conversation and a system prompt. It builds the archived tool-result lookup, creates the replay tool list, and strips the old final answer from the message history. Then it repeatedly asks the model for the next turn. If the model gives final text, it returns a ReplayResult marked not diverged. If the model asks for tools, it feeds back matching archived results and continues. If a tool call cannot be matched, or the round limit is reached, it returns a ReplayResult marked diverged with the best text seen so far.

**Call relations**: This method ties together all helpers in the file. It uses archived_tool_results, replay_tools, and replay_head for setup, then uses _feed_archived inside the turn-by-turn loop. It produces the ReplayResult that downstream grading can score, including whether the replay stayed on the archived path.

*Call graph*: calls 4 internal fn (_feed_archived, archived_tool_results, replay_head, replay_tools); 1 external calls (__init__).


### Scheduled improvement loop
Runs periodic self-improvement checks and opens governed prompt-change proposals only after repeated successful evaluation.

### `extensions/self_improvement/ufo_ext_self_improvement/cron.py`

`orchestration` · `scheduled background cron tick`

This file is the careful “heartbeat” of the self-improvement extension. On each scheduled tick, it reviews recorded agent trajectories, meaning past conversations or task runs, grouped by agent. For each agent, it either continues testing an already-open prompt candidate or opens a new one if the current prompt version has no active candidate.

The important safety idea is that this code never directly changes an agent’s prompt. It can only ask for a governed proposal, which still needs approval elsewhere. Think of it like a lab assistant: it can suggest a new recipe after repeated tests, but it cannot replace the restaurant’s recipe by itself.

Candidate progress is stored in the extension’s scoped store, keyed per agent. The stored CandidateState records which original prompt digest the candidate was based on, the proposed prompt, the task it targets, the held-out examples used for testing, how many times it has passed, and whether it is still evaluating, promoted, or rejected. A “digest” is a stable fingerprint of a prompt version. This matters because a rejected or promoted candidate is not proposed again for the same prompt version; only a later approved prompt change, which changes the digest, allows a fresh candidate.

The loop requires repeated passing ticks, controlled by stability_count, before promotion. This reduces the chance that one lucky evaluation causes a bad prompt change proposal.

#### Function details

##### `ImproveCron.run`  (lines 50–52)

```
async def run(self) -> None
```

**Purpose**: Runs one full self-improvement tick across the workspace. It gathers all known trajectories, groups them by agent, and gives each agent its own turn through the improvement process.

**Data flow**: It reads trajectories from the extension context. It groups those trajectories by agent ID, then sends each agent ID and that agent’s trajectories onward. It does not return a value; its effect is to advance stored candidates or open proposals through later steps.

**Call relations**: This is the top-level method for the cron pass. It uses _by_agent to split the shared trajectory list into per-agent batches, then calls ImproveCron._advance once for each agent batch.

*Call graph*: calls 2 internal fn (_advance, _by_agent).


##### `ImproveCron._advance`  (lines 54–60)

```
async def _advance(self, agent_id: UUID, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: Moves one agent forward by one step in the improvement process. It finds or opens a candidate for that agent’s current prompt version, then tests that candidate if one exists.

**Data flow**: It receives an agent ID and that agent’s trajectories. It reads the prompt digest from the first trajectory, builds the store key for that agent, asks for an active or newly opened candidate, and if there is one, sends it to the gate-testing step. It returns nothing; changes happen through the store or through a proposed change.

**Call relations**: ImproveCron.run calls this after grouping trajectories by agent. This method is the bridge between candidate setup in ImproveCron._active_or_open and candidate evaluation in ImproveCron._gate.

*Call graph*: calls 2 internal fn (_active_or_open, _gate); called by 1 (run).


##### `ImproveCron._active_or_open`  (lines 62–83)

```
async def _active_or_open(self, key: str, from_digest: str, trajectories: tuple[Trajectory, ...]) -> CandidateState | None
```

**Purpose**: Finds the existing candidate for an agent’s current prompt version, or creates one new candidate if it is safe and useful to do so. It prevents repeated proposals for candidates that were already rejected or promoted for the same prompt digest.

**Data flow**: It receives a store key, the current prompt digest, and the agent’s trajectories. It first checks the scoped store for a saved candidate. If that saved candidate matches the current digest and is still evaluating, it returns it; if it was already resolved, it returns nothing. If there is no usable saved candidate, it looks for task classes in the trajectories, asks the proposer for a new prompt candidate, stores the new CandidateState, and returns it.

**Call relations**: ImproveCron._advance calls this before any evaluation happens. It relies on task_classes to find meaningful task groups and on the proposer to suggest a prompt; if either cannot produce work, the improvement flow for that agent stops for this tick.

*Call graph*: called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._gate`  (lines 85–117)

```
async def _gate(self, agent_id: UUID, key: str, from_digest: str, candidate: CandidateState, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: Tests a candidate prompt and decides whether it should keep waiting, be rejected, or be turned into a governed change proposal. This is the safety gate that requires repeated successful evaluations before promotion.

**Data flow**: It receives the agent, store key, current prompt digest, candidate state, and trajectories. It builds two evaluation sets: the candidate’s own held-out examples and other held-out examples from different task classes. It asks the evaluator to compare the candidate prompt against the current prompt. If the candidate fails, it saves it as rejected. If it passes but has not passed enough consecutive ticks, it saves one more pass. If it reaches the required count, it creates an AgentChange proposal and saves the candidate as promoted with the proposal ID.

**Call relations**: ImproveCron._advance calls this after a candidate is found or opened. It uses _held_out to reconstruct test examples, calls task_classes to gather broader comparison examples, calls ImproveCron._save to persist each outcome, and hands a successful candidate to the context as an AgentChange proposal.

*Call graph*: calls 2 internal fn (_save, _held_out); called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._save`  (lines 119–131)

```
async def _save(self, key: str, candidate: CandidateState, *, status: CandidateStatus, gate_passes: int, proposal_id: str | None=None) -> None
```

**Purpose**: Writes an updated candidate state back to the scoped store. It is used whenever a candidate’s status, pass count, or proposal ID changes.

**Data flow**: It receives the store key, the old candidate state, and the new status information. It copies the candidate with the updated fields, converts it to JSON-friendly data, and stores it under the same key. It returns nothing, but the persistent record is changed.

**Call relations**: ImproveCron._gate calls this after every important verdict: rejection, continued evaluation, or promotion. This keeps the next cron tick from forgetting what happened before.

*Call graph*: called by 1 (_gate); 1 external calls (model_copy).


##### `_by_agent`  (lines 134–138)

```
def _by_agent(trajectories: tuple[Trajectory, ...]) -> Mapping[UUID, tuple[Trajectory, ...]]
```

**Purpose**: Splits a mixed list of trajectories into separate groups for each agent. This lets the improvement loop treat every agent independently.

**Data flow**: It receives all trajectories together. It builds a dictionary where each agent ID points to only that agent’s trajectories, then returns the groups as tuples so callers get stable batches. It does not change the trajectories themselves.

**Call relations**: ImproveCron.run calls this at the start of the cron tick. Its output determines how many times ImproveCron._advance runs and which trajectories each call sees.

*Call graph*: called by 1 (run).


##### `_held_out`  (lines 141–153)

```
def _held_out(trajectories: tuple[Trajectory, ...], held_out: tuple[str, ...]) -> tuple[TaskExample, ...]
```

**Purpose**: Rebuilds the candidate’s held-out test examples from the latest trajectories. It only includes examples that are still present and are identified as bad trajectories worth learning from.

**Data flow**: It receives the agent’s trajectories and a tuple of held-out conversation IDs saved in the candidate. It looks up each conversation ID, skips missing ones, asks bad_trajectory whether that trajectory contains a usable failure example, and returns the collected TaskExample objects. It does not change storage or trajectories.

**Call relations**: ImproveCron._gate calls this when preparing the candidate-specific test set for evaluation. It depends on bad_trajectory to turn a raw trajectory into a focused example the evaluator can use.

*Call graph*: called by 1 (_gate); 1 external calls (bad_trajectory).
