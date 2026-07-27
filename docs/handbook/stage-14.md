# Scheduled, recurring, billing, evaluation, and self-improvement jobs  `stage-14`

This stage is the system’s behind-the-scenes clockwork. It runs work that should happen later, repeatedly, or without a person waiting on the screen. The scheduled-tasks files let agents create recurring tasks, pause a workflow until a reply or timeout, check simple “cron” schedules, meaning repeating time rules, and safely wake due tasks without firing the same one twice. Core scheduling stores those tasks, claims them, and advances or removes them. Core jobs turns extension-declared background work into real queued runs in the right workspace.

Billing is handled by the Metronome extension, which connects usage and seat counts to Metronome and Stripe, and lets owners manage billing through chat. Evaluation support provides fake but realistic mail, calendar, and code-search connectors, so tests can run safely without touching real services.

The self-improvement pieces form a cautious improvement loop. They collect past failures into a test corpus, ask a model to propose a better prompt, replay old conversations without rerunning real tools, judge the results, and use statistical gates before opening an approval proposal. Governance then makes sure prompt changes are reviewed and not applied over newer edits.

## Files in this stage

### Self-improvement evaluation
These files build, replay, judge, and statistically gate prompt changes before they can be proposed for approval.

### `extensions/self_improvement/ufo_ext_self_improvement/evaluation.py`

`domain_logic` · `self-improvement evaluation`

This file is the evidence-gathering part of a self-improvement system. When the system invents a possible better prompt, it cannot simply trust that it sounds better. It must check whether the prompt improves real task outcomes without making other tasks worse. This file does that check in a controlled way.

The main class, CandidateEvaluation, compares two prompt versions: the current prompt, called the absent case, and the proposed prompt, called the present case. For each saved task example, it replays the task using archived messages and the same replay setup. The important idea is fairness: the task, tools, and judge stay the same, so the prompt body is the main thing being tested. This is like testing two recipes with the same ingredients and oven, so any taste difference is likely due to the recipe.

After each replay, the file asks a separate judge model to decide whether the final answer satisfies the original request. The judge must return a small JSON object saying whether the answer was accepted. These accept-or-reject results become OutcomeLabel records. Finally, the labels are passed to a two-stage gate: first checking improvement on the candidate's own task class, then checking that performance does not regress on broader held-out tasks.

#### Function details

##### `CandidateEvaluation.evaluate`  (lines 24–33)

```
async def evaluate(self, candidate_prompt: str, current_prompt: str, local_held_out: tuple[TaskExample, ...], global_held_out: tuple[TaskExample, ...]=()) -> GateVerdict
```

**Purpose**: This is the main evaluation entry for a candidate prompt. It compares the candidate prompt against the current prompt on local held-out examples and optional global held-out examples, then returns a gate verdict saying whether the candidate should pass.

**Data flow**: It receives the candidate prompt, the current prompt, a set of local saved task examples, and optionally a set of broader global examples. It turns each set into accept-or-reject labels by asking _labels to replay and judge them. It then sends the local and global labels to the two-stage gate, which produces the final pass-or-fail style verdict.

**Call relations**: This method starts the file's main workflow. It calls _labels twice: once for the candidate's own task area and once for wider regression checks. After the evidence is collected, it hands that evidence to two_stage_gate, which applies the acceptance rules.

*Call graph*: calls 1 internal fn (_labels); 1 external calls (two_stage_gate).


##### `CandidateEvaluation._labels`  (lines 35–45)

```
async def _labels(self, candidate_prompt: str, current_prompt: str, held_out: tuple[TaskExample, ...]) -> tuple[OutcomeLabel, ...]
```

**Purpose**: This helper creates the raw evidence used by the gate. For every saved task, it runs both prompt versions and records whether each resulting answer was accepted.

**Data flow**: It receives a candidate prompt, the current prompt, and a group of held-out task examples. It creates a ReplayEvaluation object using the configured replay model and round limit. For each example, it replays the task once with the current prompt and once with the candidate prompt. Each final answer is sent to _accepts, and the yes-or-no result is wrapped into an OutcomeLabel that also records whether it came from the candidate prompt. It returns all labels as an immutable tuple.

**Call relations**: This method is called by evaluate when evidence is needed for either the local or global test set. During its loop, it relies on ReplayEvaluation to regenerate an answer and on _accepts to judge that answer. The labels it builds are later consumed by the two-stage gate.

*Call graph*: calls 1 internal fn (_accepts); called by 1 (evaluate); 2 external calls (__init__, __init__).


##### `CandidateEvaluation._accepts`  (lines 47–59)

```
async def _accepts(self, request: str, answer: str) -> bool
```

**Purpose**: This helper asks the judge model whether one answer satisfies one user request. It turns the judge's reply into a simple true or false result.

**Data flow**: It receives the original request and the answer produced during replay. It sends both to the judge model with instructions to return only JSON, specifically an object with an accepted field. It then looks for a JSON object inside the judge's text, parses it, and returns true only if the parsed object says accepted is exactly true. If the judge response is missing JSON or contains invalid JSON, it safely returns false.

**Call relations**: This method is called by _labels after each replayed answer is produced. It creates the user message sent to the judge model and uses JSON parsing to convert the model's text into a dependable boolean decision for the OutcomeLabel.

*Call graph*: called by 1 (_labels); 2 external calls (__init__, loads).


### `core/src/ufo/governance.py`

`domain_logic` · `proposal creation and approval`

This file is a safety gate for changing an agent’s configuration, especially its prompt. Instead of letting code overwrite the prompt directly, it creates a proposal that says: “change this prompt from the version I saw to this new text.” The “version I saw” is stored as a digest, which is a short fingerprint made from the prompt text. If even one character changes, the fingerprint changes too.

The main idea is like signing off on edits to a shared document. When someone proposes an edit, they record which copy of the document they were looking at. Later, when the proposal is approved, the system checks whether the document is still that same copy. If it is, the edit is applied. If someone changed it in the meantime, the proposal is rejected instead of accidentally overwriting newer work.

The `Governance` class is tied to one workspace, so proposals cannot cross workspace boundaries. It records who proposed the change through the `extension` field. All database work happens inside a transaction, meaning the related reads and writes are treated as one careful unit. Approval also locks the agent row while checking and updating it, so two approvals cannot safely step on each other.

#### Function details

##### `prompt_digest`  (lines 16–17)

```
def prompt_digest(prompt: str) -> str
```

**Purpose**: Creates a stable fingerprint for a prompt. The system uses this fingerprint to tell whether a prompt is still the same text that a proposal was based on.

**Data flow**: It takes prompt text in, turns it into bytes, runs it through SHA-256, which is a standard fingerprinting algorithm, and returns the fingerprint as a text string. It does not change anything outside itself.

**Call relations**: When a change is proposed, `Governance.propose_change` uses this to record the fingerprint of the new prompt. When a proposal is approved, `Governance.approve_proposal` uses it again to compare the agent’s current prompt with the prompt version the proposal expected.

*Call graph*: called by 2 (approve_proposal, propose_change); 1 external calls (sha256).


##### `Governance.propose_change`  (lines 28–55)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: Opens a proposed prompt change for an agent in this workspace. It records the requested change as pending, but does not update the agent itself.

**Data flow**: It receives an `AgentChange`, including the target agent, the expected old prompt fingerprint, and the new prompt text. It creates a new proposal ID, checks that the agent really exists in this workspace, calculates the fingerprint of the new prompt, and writes a pending proposal row to the database. It returns a `ProposalRef`, which is a small reference containing the new proposal ID.

**Call relations**: This is the first half of the governed-change flow. Code that wants to change an agent calls this instead of editing the agent directly. It relies on `workspace_tx` for a safe database transaction, uses SQLAlchemy to read and insert rows, calls `prompt_digest` to fingerprint the new prompt, and hands the caller back a proposal reference that can later be passed to `Governance.approve_proposal`.

*Call graph*: calls 1 internal fn (prompt_digest); 5 external calls (__init__, insert, select, workspace_tx, uuid4).


##### `Governance.approve_proposal`  (lines 57–114)

```
async def approve_proposal(self, proposal_id: UUID) -> None
```

**Purpose**: Approves a pending proposal only if the agent’s prompt has not changed since the proposal was made. If the prompt has changed, it rejects the proposal rather than overwriting newer work.

**Data flow**: It receives a proposal ID, loads that proposal from the current workspace, and checks that it exists and is still pending. It then reads and locks the agent’s current prompt, calculates its fingerprint, and compares it with the proposal’s expected old fingerprint. If they differ, it marks the proposal rejected and logs that result. If they match, it writes the proposed prompt onto the agent, marks the proposal approved, and logs the approval.

**Call relations**: This is the second half of the governed-change flow. It is called after someone or something decides a proposal should be accepted. It uses `workspace_tx` so the check and update happen safely together, calls `prompt_digest` for the comparison, uses SQLAlchemy to read and update database rows, and sends outcome messages to `ufo.o11y.log` so the approval or rejection can be observed later.

*Call graph*: calls 1 internal fn (prompt_digest); 4 external calls (select, update, workspace_tx, log).


### `extensions/self_improvement/ufo_ext_self_improvement/corpus.py`

`domain_logic` · `self-improvement corpus building`

The self-improvement loop needs real examples of where the system struggled. This file looks through recorded trajectories, meaning full conversation histories, and treats a tool error as a useful “friction signal”: a sign that something went wrong and might be worth improving. It first finds the user’s original request, because that is the goal the system was trying to satisfy. It also finds the first tool result marked as an error, then traces that error back to the tool call it answered so it can name the failing tool.

Each useful failed conversation becomes a TaskExample. That example keeps the conversation id, the user request, the full message history for replay, and a plain text problem summary such as “the search tool errored: ...”. Examples are then grouped into TaskClass objects named like “tool:<name>”, so failures from the same tool are studied together.

A key safety idea is the split between “mine” and “held_out”. The mine set is what the proposer can learn from. The held-out set is kept separate for grading, like keeping some exam questions unseen while studying. Classes that do not have enough examples are ignored, because they cannot support both learning and fair checking.

#### Function details

##### `first_request`  (lines 39–43)

```
def first_request(messages: tuple[Message, ...]) -> str | None
```

*Call graph*: called by 1 (bad_trajectory).


##### `first_tool_error`  (lines 46–65)

```
def first_tool_error(messages: tuple[Message, ...]) -> tuple[str, str] | None
```

*Call graph*: called by 1 (bad_trajectory).


##### `bad_trajectory`  (lines 68–78)

```
def bad_trajectory(trajectory: Trajectory) -> tuple[str, TaskExample] | None
```

*Call graph*: calls 2 internal fn (first_request, first_tool_error); called by 1 (task_classes); 1 external calls (__init__).


##### `task_classes`  (lines 81–94)

```
def task_classes(trajectories: tuple[Trajectory, ...]) -> tuple[TaskClass, ...]
```

*Call graph*: calls 2 internal fn (_split, bad_trajectory).


##### `_split`  (lines 97–102)

```
def _split(name: str, examples: tuple[TaskExample, ...]) -> TaskClass | None
```

*Call graph*: called by 1 (task_classes); 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/cron.py`

`orchestration` · `scheduled background tick`

This file is the safety gate for automatic prompt improvement. It does not directly change an agent. Instead, it periodically reviews recent agent work, tries to create one improved prompt candidate for each agent and current prompt version, tests that candidate, and only asks the wider governance system to approve it if it passes enough times in a row.

The central idea is like a probation period. A new prompt is not trusted after one good test. It must pass the evaluation gate for `stability_count` scheduled ticks before it can be promoted into a proposal. If it fails, it is marked rejected. If it succeeds enough times, it is marked promoted and linked to the proposal that was opened.

The file also remembers candidate state in the extension's scoped store. That stored record includes which original prompt digest the candidate was based on, the proposed prompt text, the task it targets, the held-out examples used to test it, its pass count, and whether it is still being evaluated. This matters because the cron job runs repeatedly. Without stored state, it could keep proposing the same candidate again and again, or forget that a candidate already failed. A terminal candidate is suppressed until the agent prompt changes through approval, which changes the digest.

#### Function details

##### `ImproveCron.run`  (lines 50–52)

```
async def run(self) -> None
```

**Purpose**: Starts one full self-improvement tick. It gathers all available trajectories, groups them by agent, and advances the improvement process separately for each agent.

**Data flow**: It reads trajectories from the extension context. It turns the mixed list into per-agent groups, then sends each agent id and its matching trajectories onward. It returns nothing; its effect is whatever candidate state or governance proposal is created during the per-agent work.

**Call relations**: This is the top-level method for the cron pass. It uses `_by_agent` to sort the raw trajectories into agent-sized batches, then calls `ImproveCron._advance` for each batch so each agent can be checked independently.

*Call graph*: calls 2 internal fn (_advance, _by_agent).


##### `ImproveCron._advance`  (lines 54–60)

```
async def _advance(self, agent_id: UUID, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: Moves one agent forward by one step in the candidate lifecycle. It either finds or opens a candidate for the agent's current prompt version, then tests that candidate.

**Data flow**: It receives an agent id and that agent's trajectories. It reads the prompt digest from the first trajectory, builds the storage key for that agent, asks for an active candidate or opens a new one, and stops if there is no candidate to work on. If there is a candidate, it passes it to the gate evaluation step.

**Call relations**: This method is called by `ImproveCron.run` once per agent. It delegates candidate lookup or creation to `ImproveCron._active_or_open`, then hands the candidate to `ImproveCron._gate` to decide whether it should keep waiting, be rejected, or become a proposal.

*Call graph*: calls 2 internal fn (_active_or_open, _gate); called by 1 (run).


##### `ImproveCron._active_or_open`  (lines 62–83)

```
async def _active_or_open(self, key: str, from_digest: str, trajectories: tuple[Trajectory, ...]) -> CandidateState | None
```

**Purpose**: Finds the current candidate for an agent, or creates one if it is safe and useful to do so. It prevents repeated proposals for a prompt version that already has a resolved candidate.

**Data flow**: It receives a store key, the current prompt digest, and the agent's trajectories. It first checks the store for an existing candidate. If that stored candidate matches the current digest and is still evaluating, it returns it; if it is already promoted or rejected, it returns nothing. If there is no usable stored candidate, it looks for task classes in the trajectories, asks the prompt proposer for a new prompt for the first class, stores the new `CandidateState`, and returns it. If no task class or no proposal exists, it returns nothing.

**Call relations**: This method is called from `ImproveCron._advance` before any evaluation happens. It uses `task_classes` to find meaningful groups of examples and constructs a `CandidateState` when the proposer supplies a candidate prompt.

*Call graph*: called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._gate`  (lines 85–117)

```
async def _gate(self, agent_id: UUID, key: str, from_digest: str, candidate: CandidateState, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: Tests a candidate prompt and decides its next state. It rejects failing candidates, counts repeated passing ticks, and opens a governed change proposal only after enough consecutive passes.

**Data flow**: It receives the agent id, store key, original prompt digest, candidate state, and trajectories. It builds two evaluation sets: the candidate's own held-out examples and held-out examples from other task classes. It asks the evaluator to compare the candidate prompt against the current prompt. If the verdict fails, it saves the candidate as rejected with zero passes. If it passes but has not reached the required stability count, it saves the increased pass count and keeps evaluating. If it has passed enough times, it creates an `AgentChange` proposal and saves the candidate as promoted with the proposal id.

**Call relations**: This method is called by `ImproveCron._advance` after a candidate is available. It calls `_held_out` to reconstruct the candidate's held-out examples, uses `task_classes` to gather broader safety checks, calls `_save` to persist state changes, and uses the extension context's proposal mechanism to hand successful changes to governance instead of applying them directly.

*Call graph*: calls 2 internal fn (_save, _held_out); called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._save`  (lines 119–131)

```
async def _save(self, key: str, candidate: CandidateState, *, status: CandidateStatus, gate_passes: int, proposal_id: str | None=None) -> None
```

**Purpose**: Writes an updated candidate record back to the extension store. It is used whenever the gate changes a candidate's status, pass count, or proposal link.

**Data flow**: It receives the storage key, the existing candidate, the new status, the new pass count, and optionally a proposal id. It makes an updated copy of the candidate and stores it as JSON-friendly data. It returns nothing, but the persistent store now reflects the new candidate state.

**Call relations**: This helper is only called by `ImproveCron._gate`. It keeps the persistence step consistent so rejection, continued evaluation, and promotion all update the stored candidate in the same way.

*Call graph*: called by 1 (_gate); 1 external calls (model_copy).


##### `_by_agent`  (lines 134–138)

```
def _by_agent(trajectories: tuple[Trajectory, ...]) -> Mapping[UUID, tuple[Trajectory, ...]]
```

**Purpose**: Splits a mixed collection of trajectories into one group per agent. This lets the cron job evaluate each agent's prompt history separately.

**Data flow**: It receives all trajectories together. It reads each trajectory's agent id, collects trajectories with the same id into a list, converts each list to a tuple, and returns a mapping from agent id to that agent's trajectories.

**Call relations**: This helper is called by `ImproveCron.run` at the start of a tick. Its output controls how many times `ImproveCron._advance` runs and ensures one agent's data is not mixed with another's.

*Call graph*: called by 1 (run).


##### `_held_out`  (lines 141–153)

```
def _held_out(trajectories: tuple[Trajectory, ...], held_out: tuple[str, ...]) -> tuple[TaskExample, ...]
```

**Purpose**: Rebuilds the held-out examples for a candidate from the current trajectories. Held-out examples are test cases kept aside so the new prompt can be judged on work it was not built from.

**Data flow**: It receives the agent's trajectories and a tuple of held-out conversation ids stored in the candidate. It indexes the trajectories by conversation id, looks up each requested id, skips any missing trajectory, and uses `bad_trajectory` to turn relevant failed or flagged work into a `TaskExample`. It returns the collected examples as a tuple.

**Call relations**: This helper is called by `ImproveCron._gate` when preparing the evaluation input. It relies on `bad_trajectory` from the corpus code to identify usable examples, then hands those examples back to the gate so the evaluator can test the candidate prompt.

*Call graph*: called by 1 (_gate); 1 external calls (bad_trajectory).


### `extensions/self_improvement/ufo_ext_self_improvement/gate.py`

`domain_logic` · `self-improvement evaluation`

This file is the promotion gate for self-improvement. Imagine testing a new recipe against the old one: you do not switch restaurants because four customers happened to like the new dish unless the evidence is strong enough. Here, each replayed example says whether it used the candidate prompt and whether the judge accepted the answer. The file compares the candidate arm, called "present," with the current-prompt arm, called "absent."

The main question is acceptance lift: how much better the candidate's acceptance rate is than the old prompt's rate. Because small samples can be misleading, the code does not trust the raw difference alone. It builds a confidence interval, meaning a cautious range of plausible values, using Wilson bounds and a Newcombe difference method. In plain terms, it asks: "Even after accounting for uncertainty, is the candidate still clearly better?"

The gate has two stages. First, `score_gate` checks the local task class where the candidate was meant to help. Each side must have enough examples, and the cautious lower estimate of improvement must beat a minimum floor. Then `two_stage_gate` checks a broader held-out set through `global_non_inferior`. That second check is intentionally forgiving: it blocks only when there is confident evidence that the candidate makes other task classes meaningfully worse.

#### Function details

##### `wilson_lower_bound`  (lines 53–60)

```
def wilson_lower_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: This calculates a cautious lower estimate for a success rate, such as "how low might the true acceptance rate reasonably be?" It is used when the system wants to avoid being fooled by lucky small samples.

**Data flow**: It receives a number of accepted examples and a total number of examples. If there are no examples, it returns 0. Otherwise, it computes the observed success rate, adjusts it for uncertainty using the Wilson formula, and returns a lower-bound rate between 0 and 1.

**Call relations**: This is a building block for comparing two prompt arms. `lift_lower_bound` uses it to be conservative about the candidate's success rate, and `lift_upper_bound` uses it when estimating the most optimistic possible lift in the opposite direction.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `wilson_upper_bound`  (lines 63–70)

```
def wilson_upper_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: This calculates a cautious upper estimate for a success rate, such as "how high might the true acceptance rate reasonably be?" It helps the gate understand the best reasonable case for one side of a comparison.

**Data flow**: It receives accepted and total counts. If there are no examples, it returns 1, meaning the rate could be anything up to perfect because there is no evidence. Otherwise, it applies the Wilson formula and returns an upper-bound rate between 0 and 1.

**Call relations**: This supports the lift calculations. `lift_lower_bound` uses it to give the old prompt the benefit of the doubt, while `lift_upper_bound` uses it to give the candidate the benefit of the doubt when checking for possible harm.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `lift_lower_bound`  (lines 73–84)

```
def lift_lower_bound(cont: Contingency) -> float
```

**Purpose**: This estimates the lowest reasonable improvement the candidate prompt has over the old prompt. It answers the gate's core local question: "Is the candidate still better after we account for uncertainty?"

**Data flow**: It receives a `Contingency` count summary: accepted and total examples for candidate-present and candidate-absent runs. If either side has no examples, it returns 0. Otherwise, it compares the raw success rates and subtracts a combined uncertainty amount built from Wilson bounds, producing a cautious lower estimate of the lift.

**Call relations**: `score_gate` calls this after turning individual replay results into counts. Internally it calls `wilson_lower_bound`, `wilson_upper_bound`, and square-root math so it can combine the uncertainty from both arms into one direct comparison.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (score_gate); 1 external calls (sqrt).


##### `lift_upper_bound`  (lines 87–98)

```
def lift_upper_bound(cont: Contingency) -> float
```

**Purpose**: This estimates the highest reasonable improvement the candidate prompt might have over the old prompt. In this file it is mainly used to decide whether a candidate is clearly harmful on broader tasks.

**Data flow**: It receives the same counted comparison as `lift_lower_bound`. If either side has no examples, it returns 0. Otherwise, it compares the raw success rates and adds a combined uncertainty amount, producing the optimistic upper end of the lift range.

**Call relations**: `global_non_inferior` calls this when deciding whether global results show a real regression. It uses the Wilson lower and upper helpers so the global check blocks only when even the optimistic reading is worse than the allowed margin.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (global_non_inferior); 1 external calls (sqrt).


##### `contingency`  (lines 101–109)

```
def contingency(labels: tuple[OutcomeLabel, ...]) -> Contingency
```

**Purpose**: This turns a list of replay outcomes into simple counts that the statistical checks can use. It separates examples where the candidate prompt was present from examples where it was absent.

**Data flow**: It receives a tuple of `OutcomeLabel` records, each saying whether the candidate was present and whether the answer succeeded. It splits those records into present and absent groups, counts total and accepted examples in each group, and returns a `Contingency` summary.

**Call relations**: Both `score_gate` and `global_non_inferior` call this before doing their statistical comparisons. It is the bridge between raw replay labels and the count-based lift formulas.

*Call graph*: called by 2 (global_non_inferior, score_gate); 1 external calls (__init__).


##### `score_gate`  (lines 112–139)

```
def score_gate(labels: tuple[OutcomeLabel, ...], lower_bound: float=LIFT_LOWER_BOUND, n_floor: int=N_FLOOR) -> GateVerdict
```

**Purpose**: This gives the local promotion verdict for the task class the candidate is supposed to improve. It fails candidates that do not have enough replay evidence or whose cautious improvement estimate is too small.

**Data flow**: It receives replay labels, plus optional thresholds for the minimum lift and minimum number of examples per side. It summarizes the labels with `contingency`, computes the cautious lift with `lift_lower_bound`, then returns a `GateVerdict` saying pass or fail, the reason, the lift estimate, and the sample sizes.

**Call relations**: `two_stage_gate` calls this first. If `score_gate` says the candidate did not clearly improve the local task class, the larger two-stage process stops there and returns that failure.

*Call graph*: calls 2 internal fn (contingency, lift_lower_bound); called by 1 (two_stage_gate); 1 external calls (__init__).


##### `global_non_inferior`  (lines 142–154)

```
def global_non_inferior(labels: tuple[OutcomeLabel, ...], margin: float=GLOBAL_REGRESSION_MARGIN, n_floor: int=N_FLOOR) -> bool
```

**Purpose**: This checks whether the candidate avoids clearly hurting other task classes. It is not trying to prove the candidate is globally better; it only blocks when there is confident evidence of meaningful harm.

**Data flow**: It receives replay labels for the broader global set, plus an allowed regression margin and minimum sample size. It summarizes the labels with `contingency`. If either side has too few examples, it returns true because there is not enough evidence to prove harm. Otherwise, it computes the optimistic lift upper bound and returns whether that value is still above the negative regression margin.

**Call relations**: `two_stage_gate` calls this only after the local gate has passed. It hands off the statistical optimism check to `lift_upper_bound` so a candidate is rejected only when the broad results look bad even under a generous reading.

*Call graph*: calls 2 internal fn (contingency, lift_upper_bound); called by 1 (two_stage_gate).


##### `two_stage_gate`  (lines 157–174)

```
def two_stage_gate(local_labels: tuple[OutcomeLabel, ...], global_labels: tuple[OutcomeLabel, ...]) -> GateVerdict
```

**Purpose**: This is the full promotion decision. It requires both a clear local win and no clear global regression before allowing a candidate prompt to pass.

**Data flow**: It receives local replay labels and global replay labels. First it sends the local labels to `score_gate`. If that fails, it returns the local failure verdict unchanged. If the local check passes, it sends the global labels to `global_non_inferior`. A global failure is converted into a failing `GateVerdict`; otherwise the successful local verdict is returned.

**Call relations**: This function ties the file's two safeguards together. It uses `score_gate` as the first filter and `global_non_inferior` as the safety check, ensuring a prompt cannot be promoted just because it helped one area while damaging others.

*Call graph*: calls 2 internal fn (global_non_inferior, score_gate); 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/model.py`

`io_transport` · `cross-cutting model calls during proposing, replay, and grading`

This file is a narrow adapter around the project’s model access layer. In plain terms, it is like a standard plug shape: the self-improvement code can plug into a model without caring about the larger wiring behind the wall.

It defines two small promises, called protocols. A protocol is a lightweight interface: it says, “anything with this method can be used here.” `ModelLeg` promises a `complete` method for asking the model to produce plain text. `ReplayLeg` promises a `turn` method for asking the model to produce a full chat message, optionally using tools.

`ModelAccessLeg` is the real adapter. It wraps an SDK `ModelAccess` object, which knows which model to use and how model usage is metered. When someone asks it for a completion or a turn, it builds a `ModelRequest` with the system instructions, conversation messages, token limit, and tool list when needed. It also turns reasoning off and caps output at 2048 tokens.

Without this file, different parts of the extension would have to know SDK request details themselves. That would make model calls easier to get wrong and harder to keep consistent.

#### Function details

##### `ModelLeg.complete`  (lines 13–13)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This is the promised shape for anything that can ask the model for a plain text answer. Code can depend on this small promise instead of depending on one specific model implementation.

**Data flow**: It takes system instructions and a tuple of chat messages as input. An implementation is expected to send those to a model and return the model’s text as a string. This protocol method itself does not perform the work; it describes what the work must look like.

**Call relations**: This is the contract that a concrete class such as `ModelAccessLeg` can satisfy. Parts of the self-improvement flow that only need text completion can be written against this contract, so they do not need to know about SDK request objects.


##### `ReplayLeg.turn`  (lines 17–19)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This is the promised shape for anything that can ask the model to take one replay-style turn. Unlike a plain completion, the result is a full message, and the model may be given tool descriptions it can use.

**Data flow**: It takes system instructions, prior conversation messages, and available tool schemas. An implementation is expected to send that information to a model and return the next `Message`. This protocol method only defines the expected input and output shape.

**Call relations**: This gives replay code a small, stable contract to call. `ModelAccessLeg.turn` is the concrete version in this file that fulfills the contract by building an SDK model request.


##### `ModelAccessLeg.complete`  (lines 28–37)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This asks the SDK-backed model for a plain text completion using the extension’s standard settings. It is used when the caller wants text back, not a full tool-aware chat turn.

**Data flow**: It receives system instructions and conversation messages. It wraps them in a `ModelRequest`, adding the selected model name, a 2048-token output cap, and `reasoning="off"`. It sends that request through the wrapped `ModelAccess` object and returns the resulting text string.

**Call relations**: This is the concrete fulfillment of the `ModelLeg.complete` contract. Its key handoff is to `ModelRequest.__init__`, which packages the request fields into the SDK’s expected form before the request is passed to `self.model.complete`.

*Call graph*: 1 external calls (__init__).


##### `ModelAccessLeg.turn`  (lines 39–51)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This asks the SDK-backed model to produce the next full message in a tool-aware replay. It is used when the caller needs a structured chat message, possibly influenced by available tools.

**Data flow**: It receives system instructions, conversation messages, and tool schemas. It builds a `ModelRequest` that includes the selected model, the same 2048-token cap, the tools, and `reasoning="off"`. It sends that request through the wrapped `ModelAccess` object and returns the model’s `Message`.

**Call relations**: This is the concrete fulfillment of the `ReplayLeg.turn` contract. It first hands the gathered inputs to `ModelRequest.__init__` so the SDK receives a properly shaped request, then passes that request to `self.model.turn` to get the next replay message.

*Call graph*: 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/proposer.py`

`domain_logic` · `self-improvement proposal phase`

This file is part of a self-improvement loop for an AI agent. Its job is not to decide whether a new prompt is safe or good enough; it only proposes a possible rewrite. Think of it like a first draft writer: it reads the current instructions, looks at a small pile of “this went badly” examples, and suggests a clearer version of the instructions that might prevent the same problem next time.

The main piece is `PromptProposer`, which owns a `ModelLeg`. A `ModelLeg` is the connection to a language model for one step of the process. When asked to propose a change, it first checks whether the task class has mined examples. If there are no examples, there is nothing useful to learn from, so it returns nothing.

If examples exist, it builds a careful request for the model. That request includes the task class name, the current system prompt, and a limited number of example requests and problems. The system instruction tells the model to make the smallest useful change, preserve the agent’s broader behavior, and return only the full revised prompt.

After the model replies, the file cleans off common formatting clutter such as Markdown code fences. If the answer is empty or exactly the same as the current prompt, it is treated as a no-op and discarded. Otherwise it returns a `PromptCandidate`, which records the task class and the proposed new prompt.

#### Function details

##### `PromptProposer.propose`  (lines 33–43)

```
async def propose(self, current_prompt: str, task_class: TaskClass) -> PromptCandidate | None
```

**Purpose**: This is the main entry point for creating a possible improved system prompt. It uses the current prompt and a task class with failure examples to ask a model for a revised prompt, then rejects empty or unchanged answers.

**Data flow**: It receives the agent’s current system prompt and a `TaskClass`, which includes the task name and mined examples of friction. If there are no examples, it stops and returns `None`. Otherwise it builds a model request with `_prompt`, sends that request through `self.model.complete`, cleans the model’s text with `_clean`, and compares the result with the original prompt. If the cleaned result is useful and different, it returns a `PromptCandidate` containing the task class name and the new prompt text; otherwise it returns `None`.

**Call relations**: This function drives the file’s whole flow. It calls `PromptProposer._prompt` to prepare the message that the model will see, wraps that message in a `Message` object for the model API, then calls `_clean` after the model answers. When a real change is found, it creates a `PromptCandidate` so later parts of the self-improvement system can evaluate or apply the proposal.

*Call graph*: calls 2 internal fn (_prompt, _clean); 2 external calls (__init__, __init__).


##### `PromptProposer._prompt`  (lines 45–56)

```
def _prompt(self, current_prompt: str, task_class: TaskClass) -> str
```

**Purpose**: This builds the user-facing instruction text that is sent to the model. It lays out the task class, the current system prompt, and a short set of examples showing what went wrong.

**Data flow**: It receives the current prompt and a `TaskClass`. From the task class, it reads the name and the mined examples, keeping only up to the configured maximum number of examples and trimming each request and problem to the configured character limit. It turns those pieces into one readable text block and returns that block as a string.

**Call relations**: `PromptProposer.propose` calls this right before contacting the model. Its output becomes the content of the user message, while the fixed proposer system instruction supplies the model’s overall role and rules.

*Call graph*: called by 1 (propose).


##### `_clean`  (lines 59–68)

```
def _clean(text: str) -> str
```

**Purpose**: This removes simple formatting that a model may add around its answer, especially Markdown code fences. It helps turn the model’s response into plain prompt text that can be compared and stored.

**Data flow**: It receives raw text from the model. It trims whitespace from the beginning and end. If the text starts with a triple-backtick code fence, it removes the opening fence and, when present, the closing fence, then trims the result again. It returns the cleaned prompt body as a string.

**Call relations**: `PromptProposer.propose` calls this after the model returns text. The cleaned result is then checked for two failure cases: an empty answer or an answer that is unchanged from the current prompt.

*Call graph*: called by 1 (propose).


### `extensions/self_improvement/ufo_ext_self_improvement/replay.py`

`domain_logic` · `self-improvement evaluation`

This file solves a careful testing problem: how can you ask, “Would a different prompt have produced a better final answer?” without rerunning tools, touching outside systems, or changing the facts the model saw? It does this through a “replay.” A replay is like restaging a play with a new director’s note, while keeping the same props and recorded offstage answers.

The archived conversation is first trimmed so the original final answer is removed, but the earlier user messages, tool calls, and tool results remain available as context. The file then builds a small catalog of only the tools that were used in that archived run. When the model asks to use a tool during replay, the code does not execute anything. Instead, it looks up the matching old tool result from the archive and feeds that back.

If the model asks for a tool call that does not match the archive, the replay is marked as “diverged.” That means it has left the known path, so the system cannot safely provide a stored answer. The partial answer is still returned so a grader can judge it, but it is treated as a weaker signal. A round limit prevents the replay from looping forever.

#### Function details

##### `_canonical_input`  (lines 35–36)

```
def _canonical_input(value: object) -> str
```

**Purpose**: This helper turns a tool’s input into a stable text key. It makes sure the same input object is written the same way every time, so archived and replayed tool calls can be compared reliably.

**Data flow**: It receives any input value from a tool call. It converts that value into compact JSON text with keys sorted into a consistent order. The result is a string that can be used as part of a lookup key.

**Call relations**: When archived tool results are indexed, archived_tool_results uses this helper to record the exact shape of each old tool call. Later, _feed_archived uses the same helper on replayed tool calls, so both sides speak the same matching language.

*Call graph*: called by 2 (_feed_archived, archived_tool_results); 1 external calls (dumps).


##### `replay_head`  (lines 39–52)

```
def replay_head(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This function prepares the archived conversation for replay by removing the original final answer. The goal is to keep the useful context but force the model to generate its own new ending under the candidate prompt.

**Data flow**: It receives the full archived message history. Starting from the end, it removes trailing assistant messages that are plain final answers rather than tool-use requests. It returns the shortened message history that replay should begin from.

**Call relations**: ReplayEvaluation.replay calls this near the start of a replay. The returned conversation becomes the starting point passed to the model, so the model sees the old task and tool context but not the old final answer it is supposed to replace.

*Call graph*: called by 1 (replay).


##### `archived_tool_results`  (lines 55–77)

```
def archived_tool_results(messages: tuple[Message, ...]) -> dict[tuple[str, str], ToolResultBlock]
```

**Purpose**: This function builds the lookup table that lets replay answer tool calls from history instead of running tools again. It connects each archived tool request to the archived result that answered it.

**Data flow**: It reads every message in the archived conversation. First it collects tool results by their tool-use id. Then it finds each tool-use block, matches it to its result, and stores that result under a key made from the tool name and normalized input. It returns a dictionary of archived answers ready for replay.

**Call relations**: ReplayEvaluation.replay calls this before asking the model to replay the task. _feed_archived later depends on this lookup table to decide whether a replayed tool call matches the archived path and, if it does, to return the old result.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay).


##### `replay_tools`  (lines 80–97)

```
def replay_tools(messages: tuple[Message, ...]) -> tuple[ToolSchema, ...]
```

**Purpose**: This function creates the small tool list shown to the model during replay. It includes only the distinct tools that appeared in the archived conversation, which keeps replay scoped to the old path.

**Data flow**: It scans the archived messages for tool-use blocks and records each tool name once, in the order first seen. For each name it creates a permissive tool schema, meaning a description of a callable tool that accepts object-shaped input without strict argument rules. It returns these schemas as a tuple.

**Call relations**: ReplayEvaluation.replay calls this before each replay run begins. The resulting tool catalog is passed to the model turn, giving the model enough information to reproduce old calls while avoiding access to the live system’s full tool registry.

*Call graph*: called by 1 (replay); 1 external calls (__init__).


##### `_feed_archived`  (lines 100–116)

```
def _feed_archived(tool_uses: tuple[ToolUseBlock, ...], results: Mapping[tuple[str, str], ToolResultBlock]) -> Message | None
```

**Purpose**: This function answers the model’s replayed tool calls using stored archive results. If even one requested call cannot be found in the archive, it reports that replay can no longer safely continue on the old path.

**Data flow**: It receives the tool calls requested in the current model turn and the archived-result lookup table. For each call, it builds the same lookup key used during indexing. If a matching archived result exists, it creates a new tool-result block tied to the current call id but carrying the old content and error flag. If all calls match, it returns one user message containing those results; if any call is missing, it returns nothing.

**Call relations**: ReplayEvaluation.replay calls this whenever the model asks to use tools. A returned message is appended to the replay conversation so the model can continue. A missing result tells ReplayEvaluation.replay that the model has diverged from the archive.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay); 2 external calls (__init__, __init__).


##### `ReplayEvaluation.replay`  (lines 129–150)

```
async def replay(self, archived: tuple[Message, ...], system_prompt: str) -> ReplayResult
```

**Purpose**: This is the main replay procedure. It reruns the model side of one archived task under a supplied system prompt, while feeding back archived tool results instead of executing tools.

**Data flow**: It receives an archived conversation and a system prompt to test. It prepares archived tool answers, builds the replay-only tool list, and removes the original final answer from the conversation. Then it repeatedly asks the model for the next turn. If the model gives a final text answer with no tool calls, it returns that answer as a successful replay. If the model asks for tools, it tries to feed archived results back. If the call is unknown, or if the round limit is reached, it returns the best partial text and marks the replay as diverged.

**Call relations**: This method ties together all the helpers in the file. It uses archived_tool_results, replay_tools, and replay_head for setup, then relies on _feed_archived during each model round. Its final ReplayResult is what the rest of the self-improvement system can grade when comparing prompt candidates.

*Call graph*: calls 4 internal fn (_feed_archived, archived_tool_results, replay_head, replay_tools); 1 external calls (__init__).


### Scheduled job runtime
These files provide the generic background-job and durable scheduling machinery that wakes workers, claims due work, and advances or retires scheduled items.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/runner.py`

`orchestration` · `recurring scheduled tick`

This file is the “clock tick” for scheduled tasks. Think of it like a caretaker who walks down a hallway every few minutes: it checks which reminders are due, marks the ones it is about to work on so no other caretaker grabs them, skips anything that has expired, and starts the real work for the rest.

The main piece is ScheduledTaskRunner. It depends on an ExtensionContext, which must contain a scheduler. The scheduler is the storage-and-control layer that knows how to claim due tasks, retire expired ones, invoke a task inside its conversation, and move repeating tasks to their next date.

On each run, the runner records the current time, asks the scheduler for due tasks, and processes each one. A short lease is used when claiming tasks. A lease is a temporary hold, like putting a sticky note on a job saying “I’m working on this,” so overlapping runner ticks do not fire the same task twice.

For each task, the runner first checks whether it expired before it could be fired. If it is still valid, it may calculate the next cron fire time. A cron schedule is a compact rule such as “every day at 9.” If the next repeat would fall after the task’s expiry, this fire is treated as the final one, and the model receives a special instruction to finish and ask the user what to do next. Failed fires are reported together so the surrounding job system can notice and retry later.

#### Function details

##### `ScheduledTaskRunner.run`  (lines 36–47)

```
async def run(self) -> None
```

**Purpose**: This is the top-level tick of the scheduled-task runner. It checks that a scheduler exists, claims tasks that are due right now, fires each one, and reports any failures at the end.

**Data flow**: It starts with the runner’s context and reads the scheduler from it. It captures the current time, asks the scheduler for due tasks using a temporary lease, then sends each claimed task into _fire. If _fire returns a failure label, run collects it. When all tasks are processed, it either finishes quietly or raises one error listing the tasks that failed.

**Call relations**: This is the method the recurring extension job calls when the clock says it is time to poll. It delegates the actual per-task decision making to ScheduledTaskRunner._fire, while it stays responsible for the overall batch: finding due tasks, looping through them, and surfacing failures.

*Call graph*: calls 1 internal fn (_fire); 1 external calls (now).


##### `ScheduledTaskRunner._fire`  (lines 49–83)

```
async def _fire(self, scheduler: ScheduleStore, task: ScheduledTask, tick_at: datetime, expiry_checked_at: datetime) -> str | None
```

**Purpose**: This processes one claimed scheduled task. It decides whether the task should be retired, invoked now, or rescheduled for its next repeat.

**Data flow**: It receives the scheduler, the claimed task, the tick time, and the time used for the expiry check. First it asks the scheduler to retire the task if it has expired. If not expired, it works out the next fire time for repeating schedules. If this looks like the last allowed fire before expiry, it prepares a special final-fire instruction for the task. It then asks the scheduler to invoke the task. If invocation fails, it returns a short failure description. If invocation is accepted and the task repeats, it asks the scheduler to save the next fire time. The result is either no failure, meaning success or harmless no-op, or a string naming the failed task and error type.

**Call relations**: ScheduledTaskRunner.run calls this once for each due task it claimed. Inside, this function hands storage-sensitive decisions to the scheduler: retiring expired tasks, invoking the task, and rescheduling repeats. It also calls the cron helper next_fire when it needs to translate a repeating schedule rule into the next actual date.

*Call graph*: calls 3 internal fn (invoke, reschedule, retire_if_expired); called by 1 (run); 1 external calls (next_fire).


### `core/src/ufo/jobs.py`

`orchestration` · `startup and background scheduled work`

This file is the project’s background-jobs control room. At startup, the app discovers core jobs and extension-provided jobs, gives each one a stable name, and registers it with DBOS, the durable workflow system used here to keep queued work reliable across restarts. Without this file, source syncing, page-change hooks, turn recovery, and sandbox cleanup would either not run or would run in unsafe ways, such as outside the correct workspace or several times at once.

The main pattern is: a scheduled “tick” finds which workspaces actually have work, then enqueues one real job run per workspace. This is like a mail carrier checking which houses have mail before making deliveries. A slow job in one workspace does not block the others, and duplicate ticks for the same workspace are folded together.

The file also defines several core jobs. `TurnDispatcher` finds queued or parked conversation turns and offers them to the worker queue. `SandboxReaper` destroys idle disposable sandboxes so unused containers do not live forever. `PageChangeRunner` feeds changed source pages to extension hooks using per-hook cursors, so each consumer resumes where it left off. `JobRunner` ties all jobs together at boot, registers schedules, and is the only path that actually runs handlers inside a bound workspace and extension context.

#### Function details

##### `TurnDispatcher.run`  (lines 126–141)

```
async def run(self) -> None
```

**Purpose**: Finds turns that are ready to be sent to the turn worker queue, then sends only the ones allowed to run. Parked turns are rechecked for seat access and spending permission before they are released.

**Data flow**: It starts with dispatchable turn rows from the database. For parked rows, it reads seat and spending state for the relevant workspace and member; rows that still fail those gates are skipped. The remaining rows are stamped and enqueued for turn processing.

**Call relations**: This is the top-level action for the turn-dispatch background job. It asks `_dispatchable_turns` for candidates, uses seat and spend checks when needed, and hands each accepted turn to `_enqueue`.

*Call graph*: calls 2 internal fn (_dispatchable_turns, _enqueue); 4 external calls (__init__, __init__, workspace_tx, gate_member).


##### `TurnDispatcher.candidate_workspaces`  (lines 143–151)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: Finds which workspaces contain turns that may need dispatching. This prevents the scheduler from opening every workspace when only a few have pending work.

**Data flow**: It computes a grace-period cutoff time, scans the owner-level database view for workspaces with eligible queued or parked turns, and returns their workspace IDs.

**Call relations**: The job fan-out system calls this before running the turn-dispatch job. It uses `_eligible` to describe the database condition that also guides the later workspace-local scan.

*Call graph*: calls 1 internal fn (_eligible); 4 external calls (now, timedelta, select, owner_tx).


##### `TurnDispatcher._dispatchable_turns`  (lines 153–192)

```
async def _dispatchable_turns(self) -> tuple[_DispatchTurn, ...]
```

**Purpose**: Reads a small batch of turns in the current workspace that are ready to be offered to workers. It keeps conversation order by choosing only the earliest eligible turn in each conversation state.

**Data flow**: It reads turn and conversation rows from the workspace database, filters them through the same eligibility rule used by the fleet-wide scan, orders them predictably, and returns `_DispatchTurn` objects containing the fields needed for gating and enqueueing.

**Call relations**: `TurnDispatcher.run` calls this first. The returned objects become the input to the gating checks and then to `_enqueue`.

*Call graph*: calls 1 internal fn (_eligible); called by 1 (run); 6 external calls (__init__, now, timedelta, case, select, workspace_tx).


##### `TurnDispatcher._enqueue`  (lines 194–224)

```
async def _enqueue(self, turn: _DispatchTurn) -> None
```

**Purpose**: Safely stamps one turn as offered and then places it on the DBOS turn queue. The stamp stops two dispatchers from offering the same stale turn at the same time.

**Data flow**: It receives one `_DispatchTurn`, rechecks that the row is still in the same state, still stale, and still first in order, then updates its dispatch timestamp. If that update succeeds, it builds enqueue options and submits the turn to the worker queue; if not, nothing is emitted.

**Call relations**: `TurnDispatcher.run` calls this for each approved turn. It relies on `_first_in_status` and `_stale` to make the database update safe before handing work to DBOS.

*Call graph*: calls 2 internal fn (_first_in_status, _stale); called by 1 (run); 5 external calls (now, timedelta, update, workspace_tx, uuid4).


##### `TurnDispatcher._eligible`  (lines 226–234)

```
def _eligible(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database test for whether a turn should be considered by the dispatcher. Eligible means queued or parked, not recently offered, and first in line for its conversation and status.

**Data flow**: It takes a cutoff time and combines simpler database conditions into one expression. The result is not a Python boolean; it is a SQLAlchemy expression used inside database queries.

**Call relations**: Both `candidate_workspaces` and `_dispatchable_turns` use this so the fleet-wide scan and the workspace-local scan agree about what counts as dispatchable.

*Call graph*: calls 2 internal fn (_first_in_status, _stale); called by 2 (_dispatchable_turns, candidate_workspaces); 2 external calls (and_, or_).


##### `TurnDispatcher._stale`  (lines 236–240)

```
def _stale(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database test for whether a turn has not been offered recently. This lets the system retry work if a process stamped a row but failed before enqueueing it.

**Data flow**: It takes a cutoff time and returns a database condition matching rows with no dispatch timestamp or a timestamp older than that cutoff.

**Call relations**: `_eligible` uses this when searching, and `_enqueue` uses it again during the final update so a recently claimed row is not claimed twice.

*Call graph*: called by 2 (_eligible, _enqueue); 1 external calls (or_).


##### `TurnDispatcher._first_in_status`  (lines 242–251)

```
def _first_in_status(self, status: TurnStatus) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database test that a turn is the earliest turn of its status in its conversation. This protects conversation order, so later turns cannot jump ahead.

**Data flow**: It takes a turn status, creates a database subquery looking for any earlier turn with the same workspace, conversation, and status, and returns a condition that is true only when no such earlier row exists.

**Call relations**: `_eligible` uses it while finding candidates, and `_enqueue` uses it again during the atomic stamp step to preserve the same ordering under races.

*Call graph*: called by 2 (_eligible, _enqueue); 2 external calls (exists, select).


##### `SandboxReaper.run`  (lines 281–291)

```
async def run(self) -> None
```

**Purpose**: Destroys disposable sandboxes for conversations that have been idle long enough. This saves container resources while leaving durable workspace data intact so the next turn can recreate the sandbox.

**Data flow**: It reads idle sandbox handles in the bound workspace, ignores handles belonging to another backend, checks again that the conversation did not become active, asks the carrier to destroy the sandbox, and clears the stored handle from the conversation row.

**Call relations**: This is the top-level action for the sandbox cleanup job. It gets candidates from `_idle_sandboxes`, protects active conversations with `_now_active`, destroys through the carrier, and finishes with `_clear`.

*Call graph*: calls 3 internal fn (_clear, _idle_sandboxes, _now_active); 2 external calls (__init__, sandbox_handle_id).


##### `SandboxReaper.candidate_workspaces`  (lines 293–312)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: Finds workspaces that have at least one idle sandbox handle worth checking. This keeps the reaper from entering workspaces that have nothing to clean up.

**Data flow**: It computes the idle cutoff time, scans conversation rows from an owner-level database view, filters to conversations with sandbox handles and no recent or in-flight turns, and returns distinct workspace IDs.

**Call relations**: The job runner calls this before launching workspace-specific reaper work. It shares the `_active_since` condition with the workspace-local scan.

*Call graph*: calls 1 internal fn (_active_since); 4 external calls (now, timedelta, select, owner_tx).


##### `SandboxReaper._clear`  (lines 314–320)

```
async def _clear(self, conversation_id: UUID) -> None
```

**Purpose**: Removes the stored sandbox handle from a conversation after the sandbox has been destroyed. This prevents later turns from trying to attach to a dead or released container.

**Data flow**: It receives a conversation ID, opens the current workspace transaction, and updates that conversation row so `sandbox_handle` becomes empty.

**Call relations**: `SandboxReaper.run` calls this after the carrier destroy call succeeds.

*Call graph*: called by 1 (run); 2 external calls (update, workspace_tx).


##### `SandboxReaper._now_active`  (lines 322–340)

```
async def _now_active(self, conversation_id: UUID) -> bool
```

**Purpose**: Checks immediately before deletion whether a conversation has become active again. This avoids destroying a sandbox that a just-admitted turn may be using.

**Data flow**: It receives a conversation ID, looks for any non-terminal turn in that conversation, and returns true if such a turn exists.

**Call relations**: `SandboxReaper.run` calls this after reading the idle snapshot but before destroying the sandbox, because the external container destroy operation cannot be part of the same database transaction.

*Call graph*: called by 1 (run); 2 external calls (select, workspace_tx).


##### `SandboxReaper._idle_sandboxes`  (lines 342–361)

```
async def _idle_sandboxes(self) -> tuple[tuple[UUID, str], ...]
```

**Purpose**: Lists sandbox handles in the current workspace whose conversations look idle. This is the workspace-local version of the cleanup search.

**Data flow**: It computes the idle cutoff, reads conversations with stored sandbox handles, excludes conversations with active or recently updated turns, and returns pairs of conversation ID and stored handle string.

**Call relations**: `SandboxReaper.run` uses this as its starting list. It relies on `_active_since` to describe what counts as still busy.

*Call graph*: calls 1 internal fn (_active_since); called by 1 (run); 4 external calls (now, timedelta, select, workspace_tx).


##### `SandboxReaper._active_since`  (lines 363–375)

```
def _active_since(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database test for whether a conversation is busy or recently touched. The reaper uses the opposite of this test to find idle conversations.

**Data flow**: It takes a cutoff time and returns a SQLAlchemy condition matching conversations with any in-flight turn or any turn updated at or after the cutoff.

**Call relations**: Both `candidate_workspaces` and `_idle_sandboxes` use this so the broad workspace search and the bound workspace cleanup agree.

*Call graph*: called by 2 (_idle_sandboxes, candidate_workspaces); 3 external calls (exists, or_, select).


##### `_page_beyond_cursor`  (lines 378–390)

```
def _page_beyond_cursor(updated_at: datetime, page_id: UUID, cursor: object) -> bool
```

**Purpose**: Decides whether a page change is newer than a stored cursor. A cursor is the bookmark that tells a page-change consumer where it last stopped.

**Data flow**: It receives a page update time, page ID, and stored cursor value. If the cursor is missing or malformed as a non-string, it treats the page as pending; otherwise it compares timestamp first and page ID second, and returns true if the page lies after the cursor.

**Call relations**: `PageChangeRunner.workspaces_with_changes` uses this after reading each workspace’s newest page and stored cursor.

*Call graph*: called by 1 (workspaces_with_changes); 3 external calls (fromisoformat, replace, UUID).


##### `PageChangeRunner.consumers`  (lines 448–472)

```
def consumers(self) -> tuple[PageChangeConsumer, ...]
```

**Purpose**: Finds all registered extension hooks that want to be notified about page changes. It also makes sure two hooks in the same extension do not accidentally share the same cursor name.

**Data flow**: It scans manifests, collects hooks whose event is `page_change`, records the extension name, declared credential slots, hook spec, and handler-name discriminator, and returns `PageChangeConsumer` records. If a duplicate discriminator appears inside one extension, it raises an error.

**Call relations**: `core_jobs` calls this when building the set of built-in jobs, creating one scheduled job per page-change consumer.

*Call graph*: called by 1 (core_jobs); 1 external calls (__init__).


##### `PageChangeRunner.workspaces_with_changes`  (lines 474–526)

```
async def workspaces_with_changes(self, consumer: PageChangeConsumer) -> tuple[UUID, ...]
```

**Purpose**: Finds which workspaces have source pages newer than a specific page-change consumer’s cursor. This avoids running hook workflows where nothing changed.

**Data flow**: It builds a cursor key for the consumer, reads stored cursors and each workspace’s newest page from the owner-level database view, compares each newest page with that workspace’s cursor, and returns only the workspace IDs with pending changes.

**Call relations**: The page-change job candidate wrapper calls this before fan-out. It uses `_page_beyond_cursor` for the final comparison.

*Call graph*: calls 1 internal fn (_page_beyond_cursor); 2 external calls (select, owner_tx).


##### `PageChangeRunner.drive`  (lines 528–546)

```
async def drive(self, consumer: PageChangeConsumer) -> None
```

**Purpose**: Runs one page-change consumer for the currently bound workspace. It feeds changed pages to the hook in batches and advances that consumer’s cursor only after the hook succeeds.

**Data flow**: It creates the extension context, reads the stored cursor, asks the page feed for a batch of pages changed since that cursor, calls the hook with that batch, stores the next cursor, and repeats until there are no more changes or the final partial batch is done.

**Call relations**: The generated page-change job handler calls this. It uses `_context_for` to build the extension-facing tools and sends each batch to the consumer’s hook.

*Call graph*: calls 1 internal fn (_context_for); 2 external calls (__init__, __init__).


##### `PageChangeRunner._context_for`  (lines 548–563)

```
def _context_for(self, extension: str, declared: frozenset[str]) -> ExtensionContext
```

**Purpose**: Builds the `ExtensionContext` used by a page-change hook. That context is the extension’s safe toolbox: store, page feed, model access, blob access, and optional invokers.

**Data flow**: It receives the extension name and declared credential slots, reads the current workspace ID, optionally builds an invoker for that workspace, and returns a context object wired with the runner’s configured services.

**Call relations**: `PageChangeRunner.drive` calls this before invoking a hook so the hook runs with the same scoped environment as other jobs.

*Call graph*: called by 1 (drive); 2 external calls (context_for, ws_current).


##### `core_jobs`  (lines 566–635)

```
def core_jobs(sync_driver: SyncDriver, turn_dispatcher: TurnDispatcher, reaper: SandboxReaper, page_change_runner: PageChangeRunner) -> tuple[JobSpec, ...]
```

**Purpose**: Builds the list of background jobs that every deployment should run. These include source syncing, page-change consumers, turn dispatch, and sandbox cleanup.

**Data flow**: It receives the concrete runners for those core tasks, wraps each task in a `JobSpec`, asks `PageChangeRunner` for its consumers, creates one page-change job per consumer, and returns the full tuple of job specs.

**Call relations**: Startup code uses this before combining core jobs with extension jobs. The small nested functions inside it adapt each runner method to the common job-handler shape.

*Call graph*: calls 1 internal fn (consumers); 1 external calls (__init__).


##### `core_jobs._sync_sources`  (lines 583–584)

```
async def _sync_sources(context: ExtensionContext) -> None
```

**Purpose**: Adapts the source sync driver to the common job handler shape. It ignores the extension context because source syncing is already supplied by the core driver.

**Data flow**: It receives an `ExtensionContext`, does not read from it, calls the sync driver, and produces no direct return value beyond completion or failure.

**Call relations**: `core_jobs` stores this function inside the source-sync `JobSpec`, so `JobRunner.fire` eventually calls it through the normal job path.


##### `core_jobs._dispatch_turns`  (lines 586–587)

```
async def _dispatch_turns(context: ExtensionContext) -> None
```

**Purpose**: Adapts `TurnDispatcher.run` to the common job handler shape. It lets turn dispatch participate in the same scheduling and workspace binding system as other jobs.

**Data flow**: It receives an `ExtensionContext`, ignores it, runs the turn dispatcher, and returns when dispatching is complete.

**Call relations**: `core_jobs` places this in the turn-dispatch `JobSpec`; later `JobRunner.fire` invokes it inside a workspace.


##### `core_jobs._reap_sandboxes`  (lines 589–590)

```
async def _reap_sandboxes(context: ExtensionContext) -> None
```

**Purpose**: Adapts `SandboxReaper.run` to the common job handler shape. It makes sandbox cleanup a normal scheduled job.

**Data flow**: It receives an `ExtensionContext`, ignores it, runs the sandbox reaper, and returns after eligible sandboxes are cleaned.

**Call relations**: `core_jobs` places this in the sandbox-reap `JobSpec`; `JobRunner.fire` later calls it for each candidate workspace.


##### `core_jobs._drive_consumer`  (lines 592–598)

```
def _drive_consumer(consumer: PageChangeConsumer) -> Callable[[ExtensionContext], Awaitable[None]]
```

**Purpose**: Creates a job handler for one specific page-change consumer. This wrapper remembers which hook should be driven when the job fires.

**Data flow**: It receives a `PageChangeConsumer` and returns an async handler function. The returned function will later call the page-change runner for that exact consumer.

**Call relations**: `core_jobs` uses this while creating page-change `JobSpec` objects. The returned `_handler` is what the job runner invokes.


##### `core_jobs._drive_consumer._handler`  (lines 595–596)

```
async def _handler(context: ExtensionContext) -> None
```

**Purpose**: Runs the page-change cursor loop for the consumer captured by `_drive_consumer`. It is the actual job handler stored in the page-change job spec.

**Data flow**: It receives an `ExtensionContext`, does not use it directly, calls `page_change_runner.drive` for the captured consumer, and completes when that consumer has drained its pending page batch work.

**Call relations**: This function is returned by `_drive_consumer` and later called by `JobRunner.fire` as part of a page-change job.


##### `core_jobs._consumer_candidates`  (lines 600–604)

```
def _consumer_candidates(consumer: PageChangeConsumer) -> WorkspaceCandidates
```

**Purpose**: Creates a workspace-candidate function for one page-change consumer. This lets each hook run only in workspaces with changes relevant to its own cursor.

**Data flow**: It receives a `PageChangeConsumer` and returns an async function. That returned function will later ask the page-change runner for workspaces with pending changes for that consumer.

**Call relations**: `core_jobs` attaches this to each page-change `JobSpec`, pairing the consumer’s handler with its own candidate search.


##### `core_jobs._consumer_candidates._candidates`  (lines 601–602)

```
async def _candidates() -> tuple[UUID, ...]
```

**Purpose**: Finds workspaces with pending page changes for the consumer captured by `_consumer_candidates`.

**Data flow**: It takes no explicit input, uses the captured consumer, calls `page_change_runner.workspaces_with_changes`, and returns workspace IDs.

**Call relations**: The job fan-out path calls this through `JobRunner.candidates` when a page-change job tick fires.


##### `bindings_from`  (lines 646–671)

```
def bindings_from(manifests: tuple[Manifest, ...], core_jobs: tuple[JobSpec, ...]) -> tuple[_Binding, ...]
```

**Purpose**: Assigns stable names and extension scopes to all jobs. Core jobs go under the `core` namespace, while extension jobs go under their extension’s namespace.

**Data flow**: It receives extension manifests and core job specs, creates `_Binding` records with a unique key, extension name, declared credential slots, and job spec, and returns all bindings as a tuple.

**Call relations**: Startup code uses this before creating a `JobRunner`. The runner later uses these bindings to look up schedules, candidates, and handlers by key.

*Call graph*: 1 external calls (__init__).


##### `JobRunner.launch`  (lines 693–717)

```
def launch(self) -> None
```

**Purpose**: Registers all jobs with DBOS at startup. Cron-style jobs become schedules, while one-shot jobs are enqueued once with deduplication so repeated boots do not duplicate them.

**Data flow**: It stores this runner in the module-level firing slot, walks every binding, either enqueues an immediate tick or builds a schedule registration, logs what happened, and finally applies all schedules through DBOS.

**Call relations**: This is the boot-time entry into the jobs system. It prepares later calls to `job_tick` by making the active `JobRunner` discoverable.

*Call graph*: 6 external calls (now, apply_schedules, ScheduleInput, SetEnqueueOptions, log, warn).


##### `JobRunner.tick`  (lines 719–731)

```
async def tick(self, scheduled_time: datetime, key: str) -> None
```

**Purpose**: Handles one scheduled firing of a job by spreading it across the workspaces that currently have work. Each workspace gets at most one outstanding run for that job.

**Data flow**: It receives the scheduled time and job key, asks for candidate workspace IDs, and enqueues one `job_workflow` per workspace using a deduplication ID based on job key and workspace.

**Call relations**: `job_tick` calls this from the durable workflow system. It calls `candidates` first, then hands each workspace-specific run to the job queue.

*Call graph*: calls 1 internal fn (candidates); 2 external calls (SetEnqueueOptions, warn).


##### `JobRunner.candidates`  (lines 733–734)

```
async def candidates(self, key: str) -> tuple[UUID, ...]
```

**Purpose**: Looks up the candidate-workspace function for a job and runs it. This is how a job decides where it actually has work.

**Data flow**: It receives a job key, finds the matching binding, calls that binding’s `spec.candidates` function, and returns workspace IDs.

**Call relations**: `JobRunner.tick` calls this before enqueueing workspace-specific job workflows. It depends on `_binding` for the key lookup.

*Call graph*: calls 1 internal fn (_binding); called by 1 (tick).


##### `JobRunner.fire`  (lines 736–755)

```
async def fire(self, key: str, workspace_id: UUID) -> None
```

**Purpose**: Runs one job handler inside one workspace and one extension context. This is the safe path that prevents job code from running without a workspace boundary.

**Data flow**: It receives a job key and workspace ID, finds the binding, enters that workspace scope, builds an `ExtensionContext` with the configured services, calls the job handler, logs and re-raises any error.

**Call relations**: `job_workflow` calls this after DBOS dequeues a workspace-specific job. It uses `_binding` for metadata and `context_for` to prepare the handler’s environment.

*Call graph*: calls 1 internal fn (_binding); 3 external calls (context_for, log_error, ws).


##### `JobRunner._binding`  (lines 757–761)

```
def _binding(self, key: str) -> _Binding
```

**Purpose**: Finds the registered binding for a job key. It gives the rest of the runner access to the job’s schedule, candidate finder, handler, and extension scope.

**Data flow**: It receives a key, searches the runner’s binding tuple, returns the matching binding, or raises an error if the key was never registered.

**Call relations**: `JobRunner.candidates` and `JobRunner.fire` both call this before doing their work.

*Call graph*: called by 2 (candidates, fire).


##### `job_tick`  (lines 768–772)

```
async def job_tick(scheduled_time: datetime, key: str) -> None
```

**Purpose**: DBOS workflow entry for a job tick. It is the durable callback that starts fan-out from one scheduled or one-shot firing.

**Data flow**: It receives the scheduled time and job key from DBOS, reads the active module-level `JobRunner`, fails if jobs were not launched, and asks the runner to process the tick.

**Call relations**: DBOS calls this when a schedule fires or a one-shot tick is enqueued. It delegates the real logic to `JobRunner.tick`.


##### `job_workflow`  (lines 776–780)

```
async def job_workflow(scheduled_time: datetime, key: str, workspace_id: str) -> None
```

**Purpose**: DBOS workflow entry for running one job in one workspace. It is the durable unit placed on the bounded jobs queue.

**Data flow**: It receives the scheduled time, job key, and workspace ID as a string, reads the active `JobRunner`, converts the workspace ID to a UUID, and asks the runner to fire the job.

**Call relations**: `JobRunner.tick` enqueues this workflow for each candidate workspace. DBOS later runs it, and it delegates the handler execution to `JobRunner.fire`.

*Call graph*: 1 external calls (UUID).


### `core/src/ufo/scheduling.py`

`domain_logic` · `background scheduled polling and task creation/cancellation during request handling`

Scheduled work in this system is not kept only in memory. It is written as rows in the database, so a task still exists if a process restarts. This file is the main doorway to those rows. It knows how to create a scheduled task, cancel it, list it, inspect its latest result, and let a background runner safely pick up work that is due.

The central idea is a lease, like putting a temporary sticky note on a task saying, “I am working on this until this time.” `ScheduleStore.claim_due` marks due tasks with a claim so two runners do not fire the same task at once. If a claim gets too old, another runner may pick it up later. Expired tasks are deleted when they no longer need to run.

The file also treats one-time pauses specially. A pause is a scheduled wake-up for a conversation, but it must coordinate with newer human messages so the system does not resume an old turn after the conversation has already moved on. All normal task reads and writes happen inside the current workspace, so one workspace cannot accidentally see or change another workspace’s schedules.

#### Function details

##### `ScheduleInvoker.invoke_scheduled`  (lines 55–57)

```
async def invoke_scheduled(self, task: ScheduledTask, runtime_instruction: str | None=None) -> UUID | None
```

**Purpose**: This is the interface a real scheduler runner must provide to actually fire a scheduled task. It says, in effect, “given this stored task, re-enter the right conversation and maybe return the turn that was created.”

**Data flow**: It receives a `ScheduledTask` and an optional runtime instruction. An implementation uses that information to run the task in the rest of the system, then returns the created turn ID if one exists, or nothing if no turn was created.

**Call relations**: This file only defines the promise, not the implementation. `ScheduleStore.invoke` calls through to an object that follows this interface, and the scheduled task runner uses that path when it is time to fire a claimed task.


##### `_utc`  (lines 93–96)

```
def _utc(value: datetime | None) -> datetime | None
```

**Purpose**: This small helper makes database timestamps easier to compare by ensuring plain timestamps are treated as UTC time. UTC is the shared world clock used here so times from different places do not drift in meaning.

**Data flow**: It takes a datetime value or `None`. If the value is missing or already has timezone information, it leaves it alone; if it has no timezone, it labels it as UTC and returns that adjusted value.

**Call relations**: `_task` uses this when turning a database row into a `ScheduledTask`, and `ScheduleStore.inspect` uses it when returning status information. This keeps expiry times consistent for callers.

*Call graph*: called by 2 (inspect, _task); 1 external calls (replace).


##### `_claim_available`  (lines 99–103)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition for “this task can be claimed now.” A task is available if nobody has claimed it, or if its old claim has timed out.

**Data flow**: It receives the current time. It produces a SQL condition that matches rows whose `claimed_by` field is empty or whose claim expiration time is earlier than the current time.

**Call relations**: `due_task_workspaces.candidates` uses this to find workspaces that may have runnable work, and `ScheduleStore.claim_due` uses the same rule when actually leasing tasks. Using the same test in both places prevents the runner from reopening workspaces whose tasks are still under valid leases.

*Call graph*: called by 2 (claim_due, candidates); 1 external calls (or_).


##### `_expired`  (lines 106–110)

```
def _expired(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition for “this scheduled task is past its expiry time.” Expired tasks should be cleaned up instead of fired again.

**Data flow**: It receives the current time. It returns a SQL condition that matches rows with an expiry timestamp that is not empty and is at or before the current time.

**Call relations**: `due_task_workspaces.candidates` uses it to notice workspaces that need cleanup, even if no task is due to run. `ScheduleStore.claim_due` uses it to delete expired tasks before leasing runnable ones.

*Call graph*: called by 2 (claim_due, candidates); 1 external calls (and_).


##### `_task`  (lines 113–131)

```
def _task(row: sa.RowMapping) -> ScheduledTask
```

**Purpose**: This converts a raw database row into a `ScheduledTask` object that the rest of the Python code can use comfortably. It is the translation point between table fields and the in-process task value.

**Data flow**: It receives a row mapping from a database query. It copies the row’s IDs, schedule text, prompt, timing fields, claim marker, and metadata into a `ScheduledTask`, normalizing the expiry time through `_utc` on the way out.

**Call relations**: `ScheduleStore.list` uses this for tasks shown to callers, and `ScheduleStore.claim_due` uses it for tasks handed to the runner. It keeps both paths returning the same shape of task object.

*Call graph*: calls 1 internal fn (_utc); called by 2 (claim_due, list); 1 external calls (__init__).


##### `due_task_workspaces`  (lines 134–161)

```
def due_task_workspaces() -> WorkspaceCandidates
```

**Purpose**: This creates the candidate finder used by the scheduled-task runner to decide which workspaces deserve attention. Instead of scanning every workspace deeply, it first asks, “which workspaces have due or expired schedule rows?”

**Data flow**: It takes no direct input. It returns an async candidate function that, when called, reads the database and produces a tuple of workspace IDs with claimable due or expired work.

**Call relations**: The runner can use the returned function as its workspace-selection seam. The actual database lookup happens inside `due_task_workspaces.candidates`, which applies the same claim and expiry rules used later by `ScheduleStore.claim_due`.


##### `due_task_workspaces.candidates`  (lines 141–159)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: This performs the database lookup for workspaces that might need scheduled-task processing right now. It includes both tasks that are due to fire and expired tasks that should be removed.

**Data flow**: It reads the current UTC time, builds database filters for claimable and expired tasks, then queries scheduled-task rows across workspaces using an owner-level transaction. It returns only the distinct workspace IDs, not the tasks themselves.

**Call relations**: It is the inner function returned by `due_task_workspaces`. It calls `_claim_available` and `_expired` so the workspace pre-scan matches the later per-workspace claim logic.

*Call graph*: calls 2 internal fn (_claim_available, _expired); 4 external calls (now, or_, select, owner_tx).


##### `ScheduleStore.workspace_id`  (lines 173–174)

```
def workspace_id(self) -> UUID
```

**Purpose**: This property tells the store which workspace it is currently operating in. It relies on the ambient workspace context rather than accepting a workspace ID on every method.

**Data flow**: It reads the current workspace from `ws_current()` and returns that workspace’s ID. It does not change anything.

**Call relations**: Nearly every database method in `ScheduleStore` uses this value in its queries. That is what keeps a store bound to the active workspace and stops it from reading or changing another workspace’s tasks.

*Call graph*: 1 external calls (ws_current).


##### `ScheduleStore.invoke`  (lines 176–181)

```
async def invoke(self, task: ScheduledTask, runtime_instruction: str | None=None) -> UUID | None
```

**Purpose**: This asks the wired scheduler invoker to actually run a scheduled task. It is a guardrail: the store can only fire work if an invoker has been provided.

**Data flow**: It receives a `ScheduledTask` and an optional runtime instruction. If no invoker is configured, it raises an error; otherwise it forwards the task to the invoker and returns the resulting turn ID or `None`.

**Call relations**: The scheduled task runner calls this during its fire step. This method hands off from durable scheduling storage to the runtime code that re-enters the conversation.

*Call graph*: called by 1 (_fire).


##### `ScheduleStore.create`  (lines 183–218)

```
async def create(self, conversation_id: UUID, agent_id: UUID, name: str, schedule: str, prompt: str, description: str, next_run_at: datetime, created_by_member_id: UUID | None=None, expires_at: dateti
```

**Purpose**: This creates or updates a normal recurring scheduled task. It uses the task name as the stable handle, so later cancellation by name points to exactly one task in the workspace.

**Data flow**: It receives the conversation, agent, task name, schedule string, prompt, description, first run time, optional creator, and optional expiry. It rejects one-time pause schedules and reserved pause names, then calls `_upsert` to insert or update the database row and returns the resulting `ScheduledTask`.

**Call relations**: `create` is the public path for recurring tasks. It delegates the database and conflict-resolution work to `ScheduleStore._upsert`, while adding checks that keep recurring tasks separate from one-time pauses.

*Call graph*: calls 1 internal fn (_upsert).


##### `ScheduleStore.pause`  (lines 220–242)

```
async def pause(self, conversation_id: UUID, agent_id: UUID, prompt: str, description: str, next_run_at: datetime, origin_seq: int, created_by_member_id: UUID | None=None) -> ScheduledTask | None
```

**Purpose**: This creates a one-time scheduled wake-up for a conversation pause. It remembers the conversation sequence that requested the pause so the system can avoid resuming stale work after newer member activity.

**Data flow**: It receives the conversation, agent, prompt, description, wake-up time, origin sequence number, and optional creator. It builds a reserved pause name and calls `_upsert`; the result is a `ScheduledTask` if the pause was armed, or `None` if newer conversation state means the pause should not be created.

**Call relations**: `pause` is the public path for one-time workflow pauses. Like `create`, it delegates to `_upsert`, but it passes the special one-time schedule marker and the origin sequence that activates pause-specific safety checks.

*Call graph*: calls 1 internal fn (_upsert).


##### `ScheduleStore._upsert`  (lines 244–386)

```
async def _upsert(self, conversation_id: UUID, agent_id: UUID, name: str, schedule: str, prompt: str, description: str, next_run_at: datetime, origin_seq: int | None, created_by_member_id: UUID | None
```

**Purpose**: This is the shared database routine behind both recurring task creation and one-time pause creation. “Upsert” means insert a new row, or update the existing row with the same workspace and name.

**Data flow**: It receives all task definition fields, including whether this is tied to an origin conversation sequence. It first locks the conversation row, then for pauses checks whether a pending or newer member message should cancel or immediately redirect the pause. It then inserts or updates the scheduled-task row, clears any old claim, and returns a fresh `ScheduledTask`; in some pause cases it returns `None` to mean “do not arm this pause.”

**Call relations**: `ScheduleStore.create` and `ScheduleStore.pause` both call this. It is the main place where schedule definitions become durable database rows, and where pause-specific conversation safety rules are enforced before a task can be stored.

*Call graph*: called by 2 (create, pause); 6 external calls (__init__, now, exists, select, workspace_tx, uuid4).


##### `ScheduleStore.cancel`  (lines 388–396)

```
async def cancel(self, name: str) -> bool
```

**Purpose**: This removes a scheduled task by name from the current workspace. It is the normal way to stop a recurring scheduled task from firing again.

**Data flow**: It receives a task name. It deletes the matching scheduled-task row for the active workspace and returns `true` if a row was actually removed, or `false` if there was no such task.

**Call relations**: Callers use this when a user or workflow cancels a named schedule. It works directly inside a workspace transaction and does not call helper functions because the operation is a simple delete.

*Call graph*: 2 external calls (delete, workspace_tx).


##### `ScheduleStore.list`  (lines 398–414)

```
async def list(self) -> tuple[ScheduledTask, ...]
```

**Purpose**: This returns all normal recurring scheduled tasks in the current workspace. It deliberately excludes one-time pauses, because those are internal wake-ups rather than user-facing recurring schedules.

**Data flow**: It reads scheduled-task rows for the active workspace where the schedule is not the one-time marker, ordered by name. Each row is converted through `_task`, and the method returns the tasks as a tuple.

**Call relations**: This is the read path for showing or using the workspace’s defined recurring schedules. It relies on `_task` so listed tasks look the same as claimed tasks returned by `claim_due`.

*Call graph*: calls 1 internal fn (_task); 2 external calls (select, workspace_tx).


##### `ScheduleStore.claim_due`  (lines 416–470)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_TASKS) -> tuple[ScheduledTask, ...]
```

**Purpose**: This leases due tasks so a runner can safely fire them without another runner firing the same rows at the same time. It also removes expired tasks that are claimable.

**Data flow**: It receives the current time, a lease length in seconds, and a maximum number of tasks to claim. Inside one transaction, it deletes claimable expired rows, selects the oldest due non-expired rows up to the limit, marks them with a unique claim ID and claim expiry time, then returns those rows as `ScheduledTask` objects.

**Call relations**: The scheduled-task runner uses this after it has entered a workspace selected by `due_task_workspaces`. It calls `_expired`, `_claim_available`, and `_task`, and its returned claimed tasks are later passed into firing, retiring, or rescheduling steps.

*Call graph*: calls 3 internal fn (_claim_available, _expired, _task); 7 external calls (timedelta, delete, not_, select, update, workspace_tx, uuid4).


##### `ScheduleStore.retire_if_expired`  (lines 472–486)

```
async def retire_if_expired(self, task: ScheduledTask, now: datetime) -> bool
```

**Purpose**: This deletes a specific claimed task if it has expired before the runner fires it. It is a final safety check so expired work is not invoked just because it was claimed earlier.

**Data flow**: It receives a claimed `ScheduledTask` and the current time. If the task is unclaimed, it raises an error; if it is not expired, it returns `false`; if it is expired, it deletes the exact row with the matching claim and returns `true`.

**Call relations**: The scheduled task runner calls this during its fire flow before invocation. Matching both task ID and claim ID means it only retires the version this runner actually leased.

*Call graph*: called by 1 (_fire); 2 external calls (delete, workspace_tx).


##### `ScheduleStore.reschedule`  (lines 488–521)

```
async def reschedule(self, task: ScheduledTask, next_run_at: datetime, last_run_at: datetime, last_turn_id: UUID | None=None) -> bool
```

**Purpose**: This advances a recurring claimed task after it has fired. It records when it ran, optionally records the turn it created, sets the next run time, and releases the lease.

**Data flow**: It receives a claimed task, the next run time, the last run time, and optionally the turn ID produced by the fire. It refuses unclaimed tasks and one-time pauses. Then it updates the exact claimed row, clears the claim fields, clears any pause resume marker, and returns whether the row was updated.

**Call relations**: The scheduled task runner calls this after a successful recurring fire. It is paired with `claim_due`: one method leases the row before work, and this one releases and advances it afterward.

*Call graph*: called by 1 (_fire); 2 external calls (update, workspace_tx).


##### `ScheduleStore.inspect`  (lines 523–559)

```
async def inspect(self, name: str) -> TaskInspection | None
```

**Purpose**: This returns a status snapshot for one recurring scheduled task. It shows when the task will run next, when it last ran, and what happened in the latest recorded turn.

**Data flow**: It receives a task name. It looks up the matching recurring scheduled task in the current workspace and left-joins to the recorded last turn, if any. If no task exists it returns `None`; otherwise it returns a `TaskInspection` with timing fields, turn status, and the last terminal text response if present.

**Call relations**: This is the read path used when something needs to render a scheduled task’s live status. It calls `_utc` for the expiry timestamp and packages the result into `TaskInspection`.

*Call graph*: calls 1 internal fn (_utc); 3 external calls (__init__, select, workspace_tx).


### Evaluation sandbox connectors
These files define the deterministic fake connector environment used to run evaluations without touching real mailbox, calendar, or code-search systems.

### `extensions/eval_env/ufo_ext_eval_env/__init__.py`

`other` · `test and evaluation setup`

This package is for a deterministic evaluation environment. “Deterministic” means it should behave the same way every time it is run, like using a practice inbox and calendar with fixed contents instead of a live account that changes from minute to minute. That matters because evaluations need fair, repeatable conditions. If the system is being tested on tasks involving mail or calendar data, using real services would introduce noise: messages could arrive, events could change, network calls could fail, or private data could leak into a test. This package’s purpose is to offer fake connector providers for mailbox and calendar features, so the rest of the project can exercise those workflows safely and consistently. The file itself does not define functions or classes; it serves as the package’s front door and includes a short description of what the package contains.


### `extensions/eval_env/ufo_ext_eval_env/manifest.py`

`domain_logic` · `eval setup and connector tool calls`

This file creates a small fake workplace for evaluating an agent: a mailbox, a calendar, and a code search tool. The important idea is that it is not a loose mock. The agent still discovers tools, reads their schemas, and calls them through the same connector route used in production. The difference is that the data lives in workspace-scoped evaluation storage, so tests can seed a known starting state and later check exactly what changed.

The email tools can send mail and list messages. Sent mail is written to an `eval_env_email` table, so a grader can verify it later. The calendar tools create, list, update, and cancel events in an `eval_env_event` table. Cancelled events stay visible with a cancelled status, which is useful for checking behavior rather than hiding the record. Code search is different: it is read-only. Evaluation authors seed the exact search response under a query string, and the tool returns that response byte-for-byte. If no fixture was seeded, it fails loudly so a broken test setup is not mistaken for an empty search result.

The file also declares three connector providers, one each for email, calendar, and code search, plus a minimal OAuth-style login stub because the connector registry expects one.

#### Function details

##### `_transaction`  (lines 171–175)

```
def _transaction()
```

**Purpose**: Opens a database transaction tied to this evaluation extension. Code uses it whenever it needs to read or change the evaluation mailbox or calendar safely.

**Data flow**: It takes no direct input. It builds an extension context using the eval environment name, with a scoped store and no declared credentials, then returns a transaction object. Callers enter that transaction and use the resulting connection to run database queries.

**Call relations**: The email and calendar routines call this before touching their tables. It is the shared doorway through which `_send_email`, `_list_emails`, `_create_event`, `_list_events`, and `_change_event` reach durable workspace storage.

*Call graph*: called by 5 (_change_event, _create_event, _list_emails, _list_events, _send_email); 3 external calls (__init__, __init__, __init__).


##### `_moment`  (lines 178–182)

```
def _moment(value: str) -> datetime
```

**Purpose**: Turns a text timestamp into a real date-time value. If the text does not say what time zone it is in, the function treats it as UTC, meaning universal coordinated time.

**Data flow**: It receives an ISO 8601 date-time string, parses it, checks whether a time zone is present, and adds UTC if one is missing. It returns a timezone-aware `datetime` object ready to store in the calendar table.

**Call relations**: Calendar creation and calendar updates call this before saving start or end times. It keeps event times consistent no matter whether the tool caller included an explicit time zone.

*Call graph*: called by 2 (_create_event, _update_event); 1 external calls (fromisoformat).


##### `EvalEnvBroker.tools`  (lines 190–198)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Returns the tools available for one provider, optionally narrowed by a search word. This is how the connector can answer, “What can this email, calendar, or code-search provider do?”

**Data flow**: It receives a workspace id, provider name, and search text. It looks up that provider’s catalog, compares the search text with each tool’s slug and description, and returns matching tools. If there is no query, or no matches, it returns the full catalog for that provider.

**Call relations**: The broker’s `search` method calls this when the system asks for tool discovery results. It sits at the front of the evaluation connector experience, before any specific tool is executed.

*Call graph*: called by 1 (search).


##### `EvalEnvBroker.schema`  (lines 200–204)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Finds the detailed definition for one named tool. The schema tells the agent what arguments that tool accepts.

**Data flow**: It receives a workspace id, provider name, and tool slug. It scans that provider’s catalog and returns the matching `BrokerTool`. If no tool has that slug, it raises an unknown-tool error.

**Call relations**: This is used by the connector layer when a caller asks to describe a specific tool. It does not call the tool itself; it only returns the instruction card for that tool.

*Call graph*: 1 external calls (__init__).


##### `EvalEnvBroker.execute`  (lines 206–245)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs the requested evaluation tool. It is the central dispatcher that turns a provider name and tool slug into the right email, calendar, or code-search action.

**Data flow**: It receives the workspace id, provider, tool slug, raw argument data, account id, and idempotency key. It validates the raw arguments with the right input model, then calls the matching private method. It returns that method’s response dictionary, or raises an unknown-tool error if the provider and slug do not match the catalog.

**Call relations**: The connector runtime calls this after an agent chooses a tool. This function then hands off to `_send_email`, `_list_emails`, `_create_event`, `_list_events`, `_update_event`, `_cancel_event`, or `_search_code` depending on the request.

*Call graph*: calls 7 internal fn (_cancel_event, _create_event, _list_emails, _list_events, _search_code, _send_email, _update_event); 1 external calls (__init__).


##### `EvalEnvBroker._search_code`  (lines 247–255)

```
async def _search_code(self, args: SearchCodeArgs) -> dict[str, object]
```

**Purpose**: Returns a pre-seeded code search response for the exact query the agent asked for. It deliberately fails if the test did not seed that query, so evaluation mistakes are caught early.

**Data flow**: It receives validated code-search arguments containing a query string. It reads the scoped store key made from the code-search prefix plus that query. If the stored value is a dictionary, it copies and returns it; otherwise it raises an error saying no fixture exists.

**Call relations**: `execute` calls this when the code-search provider receives the `search_code` tool call. Unlike email and calendar methods, it does not use SQL tables because code search results are fixed fixtures, not mutable state.

*Call graph*: called by 1 (execute); 1 external calls (__init__).


##### `EvalEnvBroker._send_email`  (lines 257–272)

```
async def _send_email(self, workspace_id: UUID, args: SendEmailArgs) -> dict[str, object]
```

**Purpose**: Records a sent email in the evaluation mailbox. This lets an evaluation check that the agent sent the right message to the right people.

**Data flow**: It receives a workspace id and validated email arguments. It creates a new email id, opens a transaction, inserts a row into the email table with folder `sent`, the fixed assistant sender address, recipients, subject, body, and current time, then returns the new id, sent status, and recipient list.

**Call relations**: `execute` calls this for the `send_email` tool. It uses `_transaction` to make the database write durable and isolated to the extension’s storage.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 3 external calls (now, insert, uuid4).


##### `EvalEnvBroker._list_emails`  (lines 274–309)

```
async def _list_emails(self, workspace_id: UUID, args: ListEmailsArgs) -> dict[str, object]
```

**Purpose**: Reads emails from the evaluation mailbox, newest first. It can filter by folder and by a simple text query over sender, subject, and body.

**Data flow**: It receives a workspace id and validated listing arguments. It builds database conditions for the workspace and folder, optionally adds a case-insensitive substring search, reads matching rows ordered by sent time descending, and returns them as plain dictionaries with ids, addresses, subject, body, and timestamp.

**Call relations**: `execute` calls this for the `list_emails` tool. It uses `_transaction` for the read and SQL filtering to make the returned mailbox view match the requested folder and query.

*Call graph*: calls 1 internal fn (_transaction); called by 1 (execute); 2 external calls (or_, select).


##### `EvalEnvBroker._create_event`  (lines 311–325)

```
async def _create_event(self, workspace_id: UUID, args: CreateEventArgs) -> dict[str, object]
```

**Purpose**: Adds a confirmed event to the evaluation calendar. This is how an agent’s calendar creation action becomes persistent test state.

**Data flow**: It receives a workspace id and validated event details. It creates a new event id, converts start and end strings into date-time values, inserts a calendar row with confirmed status and attendees, then returns the event id and status.

**Call relations**: `execute` calls this for the `create_event` tool. It relies on `_moment` for time parsing and `_transaction` for the database insert.

*Call graph*: calls 2 internal fn (_moment, _transaction); called by 1 (execute); 2 external calls (insert, uuid4).


##### `EvalEnvBroker._list_events`  (lines 327–340)

```
async def _list_events(self, workspace_id: UUID, args: ListEventsArgs) -> dict[str, object]
```

**Purpose**: Reads calendar events for a workspace in start-time order. It can also narrow the list to event titles containing a search string.

**Data flow**: It receives a workspace id and validated listing arguments. It builds a query for that workspace, optionally filters by title, reads rows ordered by start time up to the requested limit, and converts each row into a plain event dictionary.

**Call relations**: `execute` calls this for the `list_events` tool. It uses `_transaction` to read the table and `_event_json` to shape each database row into the response format the tool returns.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 1 (execute); 1 external calls (select).


##### `EvalEnvBroker._update_event`  (lines 342–354)

```
async def _update_event(self, workspace_id: UUID, args: UpdateEventArgs) -> dict[str, object]
```

**Purpose**: Changes selected fields on an existing calendar event. It supports partial updates, so callers can change only the title, only the time, only attendees, or any combination.

**Data flow**: It receives a workspace id and validated update arguments. It builds a changes dictionary from only the fields that were provided, converting time strings when needed. If nothing was provided to change, it raises an error. Otherwise it passes the event id and changes to `_change_event` and returns the updated event.

**Call relations**: `execute` calls this for the `update_event` tool. This function prepares the requested changes, while `_change_event` does the shared database update and final lookup.

*Call graph*: calls 2 internal fn (_change_event, _moment); called by 1 (execute).


##### `EvalEnvBroker._cancel_event`  (lines 356–357)

```
async def _cancel_event(self, workspace_id: UUID, args: CancelEventArgs) -> dict[str, object]
```

**Purpose**: Marks an existing calendar event as cancelled. The event is not deleted, so evaluations can still see that cancellation happened.

**Data flow**: It receives a workspace id and validated cancel arguments containing an event id. It asks `_change_event` to set that event’s status to `cancelled`, then returns the updated event record.

**Call relations**: `execute` calls this for the `cancel_event` tool. It is a small wrapper around `_change_event`, sharing the same update-and-return behavior used by event editing.

*Call graph*: calls 1 internal fn (_change_event); called by 1 (execute).


##### `EvalEnvBroker._change_event`  (lines 359–378)

```
async def _change_event(self, workspace_id: UUID, event_id: str, changes: dict[str, object]) -> dict[str, object]
```

**Purpose**: Applies changes to one calendar event and returns the event after the change. It is the common update engine for both editing and cancelling events.

**Data flow**: It receives a workspace id, event id string, and a dictionary of fields to change. It opens a transaction, updates the matching calendar row for that workspace, checks that exactly one row changed, then reads the row back and converts it to a response dictionary. If no matching event exists, it raises an error.

**Call relations**: `_update_event` and `_cancel_event` both call this after deciding what should change. It uses `_transaction` for the database work and `_event_json` to produce the final tool response.

*Call graph*: calls 2 internal fn (_event_json, _transaction); called by 2 (_cancel_event, _update_event); 3 external calls (select, update, UUID).


##### `EvalEnvBroker._event_json`  (lines 380–388)

```
def _event_json(self, row: sa.Row) -> dict[str, object]
```

**Purpose**: Converts a calendar database row into the simple dictionary returned by calendar tools. This keeps event responses consistent across listing, updating, and cancelling.

**Data flow**: It receives a row from the calendar table. It turns the id into text, formats start and end times as ISO strings, and copies the title, attendees, and status into a dictionary. The database row is not changed.

**Call relations**: `_list_events` uses this for every listed event, and `_change_event` uses it after an update or cancellation. It is the final formatting step before calendar data leaves the broker.

*Call graph*: called by 2 (_change_event, _list_events).


##### `EvalEnvBroker.file_outputs`  (lines 390–391)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: States that evaluation environment tools do not produce downloadable files. Every result is returned directly as structured data.

**Data flow**: It receives a tool response dictionary but does not inspect it. It always returns an empty tuple, meaning there are no broker files attached to the response.

**Call relations**: The connector interface can ask brokers whether a response has file outputs. For this broker, the answer is always no, so no later file-handling step is needed.


##### `EvalEnvBroker.stage_upload`  (lines 393–402)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Rejects file uploads for these evaluation providers. Email, calendar, and code search in this environment only accept normal tool arguments, not uploaded files.

**Data flow**: It receives upload details such as workspace id, provider, tool slug, filename, MIME type, and checksum. Instead of creating an upload slot, it raises a runtime error explaining that uploads are not accepted.

**Call relations**: This exists because the broker interface includes upload staging. If any caller tries to use uploads with the eval providers, this function stops the flow immediately.


##### `EvalEnvBroker.search`  (lines 404–405)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Wraps tool discovery results in the standard broker search response object. It is a connector-friendly way to search available tools.

**Data flow**: It receives a workspace id, provider, and query string. It asks `tools` for the matching tool catalog, then places those tools inside a `BrokerSearch` result.

**Call relations**: The connector runtime can call this when searching external tools. It delegates the actual matching to `tools` and only packages the answer in the expected response type.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `EvalEnvBroker.credential`  (lines 407–408)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a simple synthetic credential for the evaluation account. This gives the connector machinery something credential-shaped without using a real secret.

**Data flow**: It receives a workspace id, provider, and account id. It builds and returns a credential whose bearer token is text in the form `eval-env:<account>`.

**Call relations**: The connector framework may ask the broker for credentials before making a call. In this evaluation broker, the credential is only a placeholder because all work happens inside local eval storage.

*Call graph*: 1 external calls (__init__).


##### `_EvalEnvOAuth.authorize_url`  (lines 419–420)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds a fake authorization web address for the evaluation provider. It satisfies the connector’s expectation that each provider has an OAuth-style connect flow.

**Data flow**: It receives a state value and redirect URI. It combines them with the provider’s fake host into an HTTPS authorization URL string and returns that string.

**Call relations**: The eval flow normally seeds grants directly, so this is rarely exercised. It is present for registry completeness alongside `_EvalEnvOAuth.exchange` and the provider declarations in `manifest`.


##### `_EvalEnvOAuth.exchange`  (lines 422–425)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Completes the fake OAuth exchange by returning the fixed evaluation account id. OAuth is a login handoff pattern where an app trades a temporary code for an account grant.

**Data flow**: It receives a code, redirect URI, workspace id, and state, but does not need their contents. It returns an `OAuthAccount` with the constant account id used by this eval environment.

**Call relations**: If the connector registry ever drives the eval provider’s connect flow, this method supplies the account object. In normal evaluations, grants are seeded directly instead.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 428–450)

```
def manifest() -> Manifest
```

**Purpose**: Declares the evaluation extension to the host system. It registers the email, calendar, and code-search providers with labels, fake OAuth descriptors, and the shared broker that runs their tools.

**Data flow**: It creates one `EvalEnvBroker`, then builds a `Manifest` containing three `ConnectorProvider` entries. Each entry names a provider through its OAuth stub, gives it a human label, and points it at the broker. The completed manifest is returned to the extension loader.

**Call relations**: This is the file’s registration point. When the extension system loads the eval environment, it calls `manifest`, and the returned object tells the host which providers exist and how their tool calls should reach `EvalEnvBroker`.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


### Billing automation
This file connects scheduled and chat-driven product flows to Metronome and Stripe for usage billing, seat counts, and billing setup.

### `extensions/metronome/ufo_ext_metronome.py`

`orchestration` · `scheduled billing/usage jobs and owner chat tool calls`

This file is the bridge between UFO’s internal records and the outside billing services. Without it, settled usage would stay inside UFO and never reach Metronome, seat counts would not be billed or reported, and workspace owners would have no chat tools for granting seats or starting billing.

It does four main jobs. First, a scheduled usage shipper reads finished usage records, turns each one into a Metronome ingest event, sends the batch, and only then marks those records as shipped. The event identifiers are stable, like writing the same tracking number on a package every retry, so a crash can safely resend without double billing. Second, a daily seat shipper records how many people currently hold seats and sends that snapshot to Metronome. Third, a seat approval job notices unseated members when the included seats are full and asks the owner in their private conversation whether to grant a paid overage seat.

The file also provides chat tools: grant a seat, revoke a seat, list seats, and manage billing. Billing setup creates or reuses a Stripe Customer, returns a Stripe Customer Portal link for saving a card, stores the intended Metronome package, and later a scheduled activation job creates the Metronome customer and contract once Stripe reports a default payment method. Provider calls use stable identities so retries reconcile with existing objects instead of creating duplicates.

#### Function details

##### `UsageShipper.run`  (lines 198–213)

```
async def run(self) -> None
```

**Purpose**: Sends one workspace’s settled usage records to Metronome in safe batches. It is built so retrying after a crash sends the same events again rather than inventing new ones.

**Data flow**: It reads the Metronome bearer token from the environment, gets the workspace’s fixed backfill floor, asks the extension context for pending usage exports, converts them into event dictionaries, posts them to Metronome, logs the shipment, and then acknowledges the exports so they are not sent again unless the post failed before acknowledgement.

**Call relations**: The scheduled `_ship` wrapper creates a `UsageShipper` and calls this method. During the run it relies on `_floor` to decide how far back to look, `_events` to shape UFO usage into Metronome events, `_ingest` to perform the HTTP send, and `_require_env` to fail early if the bearer token is missing.

*Call graph*: calls 4 internal fn (_events, _floor, _ingest, _require_env); 1 external calls (log).


##### `UsageShipper._floor`  (lines 215–225)

```
async def _floor(self) -> datetime
```

**Purpose**: Chooses the earliest usage time this workspace is allowed to ship. On the first run it stores a fixed cutoff so the initial catch-up stays inside Metronome’s allowed backfill window.

**Data flow**: It reads a saved timestamp from the workspace store. If none exists, it calculates “now minus the backfill window,” saves that timestamp, and returns it; otherwise it parses and returns the stored timestamp.

**Call relations**: Only `UsageShipper.run` calls this before asking for pending usage exports. Its result limits the records that core returns for shipping.

*Call graph*: called by 1 (run); 3 external calls (fromisoformat, now, timedelta).


##### `UsageShipper._events`  (lines 227–246)

```
def _events(self, exports: tuple[UsageExport, ...]) -> list[dict[str, object]]
```

**Purpose**: Turns internal usage export records into the exact event shape Metronome expects. Each event carries a stable transaction id so retries can be deduplicated by Metronome.

**Data flow**: It receives a tuple of `UsageExport` objects, reads the workspace id as the customer id, formats each usage time, and produces a list of dictionaries containing the usage dimension, model, amount, price details, turn id, and whether the workspace used its own provider key.

**Call relations**: It is called by `UsageShipper.run` just before `_ingest`. It delegates timestamp formatting to `_rfc3339` so all outbound times use the same format.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run).


##### `_ship`  (lines 249–250)

```
async def _ship(ctx: ExtensionContext) -> None
```

**Purpose**: Small scheduled-job entry point for usage shipping. It adapts the job runner’s workspace context into a `UsageShipper` run.

**Data flow**: It receives an `ExtensionContext`, constructs a `UsageShipper` with the configured test or production transport, and awaits its run; it returns nothing directly.

**Call relations**: The extension manifest registers this as the handler for the usage shipping job. Its only job is to hand off to `UsageShipper.run`.

*Call graph*: 1 external calls (__init__).


##### `SeatShipper.run`  (lines 263–279)

```
async def run(self) -> None
```

**Purpose**: Sends Metronome one daily snapshot of how many seats a workspace is using. It also initializes the workspace’s seat limit and included-seat count if they have not been set yet.

**Data flow**: It reads the bearer token, checks whether today’s seat snapshot was already shipped, opens a database transaction, ensures default seat settings exist, reads a seat snapshot, sends one Metronome ingest event, logs the count, and stores today’s date as shipped.

**Call relations**: The scheduled `_ship_seats` wrapper calls this. It uses `_event` to build the seat event, `_ingest` to send it, `_require_env` for configuration, and the `Seats` core service for the actual seat data.

*Call graph*: calls 3 internal fn (_event, _ingest, _require_env); 3 external calls (__init__, now, log).


##### `SeatShipper._event`  (lines 281–292)

```
def _event(self, snapshot: SeatSnapshot, today: str) -> dict[str, object]
```

**Purpose**: Builds the Metronome event for one day’s seat count. The event id is tied to the workspace and date so retries for the same day are treated as the same snapshot.

**Data flow**: It receives a `SeatSnapshot` and a date string, reads the workspace id, stamps the current time, and returns a dictionary with the seated count and seat limit as string properties.

**Call relations**: It is called by `SeatShipper.run` after the current seat snapshot is read. It uses `_rfc3339` to format the event timestamp.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run); 1 external calls (now).


##### `_ship_seats`  (lines 295–296)

```
async def _ship_seats(ctx: ExtensionContext) -> None
```

**Purpose**: Small scheduled-job entry point for daily seat-count shipping. It turns the job runner’s context into a `SeatShipper` run.

**Data flow**: It receives an `ExtensionContext`, creates a `SeatShipper` with the configured transport, awaits it, and produces no separate result.

**Call relations**: The manifest registers this for the seat shipping job. It simply hands control to `SeatShipper.run`.

*Call graph*: 1 external calls (__init__).


##### `SeatApprovals.run`  (lines 312–337)

```
async def run(self) -> None
```

**Purpose**: Finds unseated members who need owner approval for paid overage seats and asks the owner in chat. It avoids asking twice for the same member.

**Data flow**: It reads the current seat snapshot, stops if included seats are not exhausted, filters unseated members, checks a per-email marker in the store, finds the owner’s private conversation, invokes an internal prompt asking for a grant or decline decision, and then records that the ask was made.

**Call relations**: The scheduled `_ask_seat_approvals` wrapper starts this flow. It relies on the core `Seats` service for membership state and `owner_conversation` to find where to send the approval request.

*Call graph*: 3 external calls (__init__, now, owner_conversation).


##### `_ask_seat_approvals`  (lines 340–341)

```
async def _ask_seat_approvals(ctx: ExtensionContext) -> None
```

**Purpose**: Small scheduled-job entry point for seat approval prompts. It runs the approval scanner for one workspace.

**Data flow**: It receives an `ExtensionContext`, constructs `SeatApprovals`, awaits its run, and returns nothing directly.

**Call relations**: The manifest registers this as the seat approval job handler. It exists to hand off cleanly to `SeatApprovals.run`.

*Call graph*: 1 external calls (__init__).


##### `BillingConfig.from_env`  (lines 360–379)

```
def from_env(cls) -> 'BillingConfig'
```

**Purpose**: Loads the environment variables needed for billing setup and activation. It fails before any provider call if the deployment is missing required billing settings.

**Data flow**: It reads the Stripe secret key, Stripe portal configuration id, Metronome bearer token, and Metronome package alias from environment variables. If any are missing it raises one error naming all missing settings; otherwise it returns a validated `BillingConfig` object.

**Call relations**: Billing tool calls and the billing activation job call this at the start of their work. Usage and seat shipping do not use it because they only need the Metronome bearer token.


##### `BillingActivation.run`  (lines 414–441)

```
async def run(self) -> None
```

**Purpose**: Turns a workspace’s saved payment method into a live Metronome billing plan. It resumes safely if a previous job run stopped halfway.

**Data flow**: It reads the stored billing record, stops if there is no pending setup or it is already activated, loads billing config, checks Stripe for a default payment method, creates or recovers the Metronome customer, creates or recovers the Metronome contract, stores each new provider id, and finally notifies the owner.

**Call relations**: The scheduled `_activate_billing` wrapper calls this. It coordinates `_billing_record`, `_has_default_payment_method`, `_metronome_customer`, `_metronome_contract`, `_contract_key`, `_store`, and `_notify` in order.

*Call graph*: calls 7 internal fn (_notify, _store, _billing_record, _contract_key, _has_default_payment_method, _metronome_contract, _metronome_customer).


##### `BillingActivation._store`  (lines 443–445)

```
async def _store(self, record: BillingRecord) -> BillingRecord
```

**Purpose**: Persists the current billing record for a workspace. It is used after each successful provider step so the next job tick knows where to resume.

**Data flow**: It receives a `BillingRecord`, serializes it to JSON-friendly data, writes it under the billing key in the extension store, and returns the same record.

**Call relations**: Both `BillingActivation.run` and `_notify` call this after changing billing state. It is the durable checkpoint between provider calls.

*Call graph*: called by 2 (_notify, run); 1 external calls (model_dump).


##### `BillingActivation._notify`  (lines 447–465)

```
async def _notify(self, record: BillingRecord) -> None
```

**Purpose**: Tells the workspace owner that billing is active, then marks activation complete. If there is no owner conversation yet, it leaves the record pending so a later tick can try again.

**Data flow**: It looks up the owner’s private conversation, sends an internal prompt announcing the active package with a stable idempotency key, stores a copy of the record with `activated_at` set to the current time, and logs the activation.

**Call relations**: Only `BillingActivation.run` calls this after the Metronome contract exists. It uses `_store` to record the final activation mark.

*Call graph*: calls 1 internal fn (_store); called by 1 (run); 4 external calls (now, model_copy, log, owner_conversation).


##### `_activate_billing`  (lines 468–469)

```
async def _activate_billing(ctx: ExtensionContext) -> None
```

**Purpose**: Small scheduled-job entry point for billing activation. It runs the activation workflow for one workspace.

**Data flow**: It receives an `ExtensionContext`, constructs `BillingActivation` with the configured transport, awaits its run, and returns nothing directly.

**Call relations**: The manifest registers this as the billing activation job handler. It hands off to `BillingActivation.run`.

*Call graph*: 1 external calls (__init__).


##### `_billing_record`  (lines 472–474)

```
async def _billing_record(ctx: ExtensionContext) -> BillingRecord | None
```

**Purpose**: Reads the workspace’s saved billing setup state, if one exists. This is the shared way billing tools and jobs find Stripe and Metronome ids.

**Data flow**: It reads the billing key from the extension store. If nothing is stored it returns `None`; otherwise it validates the stored data into a `BillingRecord` object.

**Call relations**: It is used by billing activation, setup, status, and portal flows. Those callers use its result to decide whether billing has started and which provider objects to query.

*Call graph*: called by 4 (run, _billing_portal, _billing_setup, _billing_status).


##### `_contract_key`  (lines 477–481)

```
def _contract_key(workspace_id: UUID) -> str
```

**Purpose**: Creates the permanent identity string for this workspace’s Metronome contract. This lets the code recognize the correct contract later, even if the customer has other contracts.

**Data flow**: It receives the workspace UUID and returns a string made from a fixed prefix plus that UUID.

**Call relations**: Billing activation uses it when creating a contract, and billing status uses it when checking whether the workspace’s own plan exists.

*Call graph*: called by 2 (run, _billing_status).


##### `grant_seat`  (lines 506–512)

```
async def grant_seat(ctx: ToolContext, args: GrantSeatInput) -> ToolResult
```

**Purpose**: Chat tool that grants a seat to a workspace member by email. It is owner-only because granting may create a billable overage seat.

**Data flow**: It validates that the caller is the owner, opens a transaction, grants the requested email a seat through the core `Seats` service, reads the updated snapshot, and returns that snapshot as JSON text.

**Call relations**: The manifest exposes this as the `grant_seat` tool. It relies on `_owner_seats` for permission checking and `_snapshot_result` to format the response.

*Call graph*: calls 2 internal fn (_owner_seats, _snapshot_result).


##### `revoke_seat`  (lines 515–525)

```
async def revoke_seat(ctx: ToolContext, args: RevokeSeatInput) -> ToolResult
```

**Purpose**: Chat tool that removes a member’s seat by email. It also marks that member as already decided so the approval job does not ask the owner about them again.

**Data flow**: It validates that the caller is the owner, revokes the seat inside a transaction, reads the updated snapshot, writes a per-email approval marker with the current time, and returns the snapshot as JSON text.

**Call relations**: The manifest exposes this as the `revoke_seat` tool. It uses `_owner_seats` for permission checking and `_snapshot_result` for the tool response.

*Call graph*: calls 2 internal fn (_owner_seats, _snapshot_result); 1 external calls (now).


##### `list_seats`  (lines 528–532)

```
async def list_seats(ctx: ToolContext, args: ListSeatsInput) -> ToolResult
```

**Purpose**: Chat tool that reports the workspace’s seat limit, included allowance, overage count, and members. It does not change anything.

**Data flow**: It opens a transaction, reads the current seat snapshot for the workspace, and returns that snapshot as JSON text.

**Call relations**: The manifest exposes this as the `list_seats` tool. It uses the core `Seats` service for data and `_snapshot_result` to shape the answer.

*Call graph*: calls 1 internal fn (_snapshot_result); 1 external calls (__init__).


##### `manage_billing`  (lines 535–544)

```
async def manage_billing(ctx: ToolContext, args: ManageBillingInput) -> ToolResult
```

**Purpose**: Chat tool for owner billing actions: setup, status, or portal. It keeps billing conversations restricted to the owner’s private chat.

**Data flow**: It validates the caller and conversation through `_owner_billing`, loads billing configuration, branches on the requested action, and returns the result from setup, status, or portal handling.

**Call relations**: The manifest exposes this as the `manage_billing` tool. It delegates the actual work to `_billing_setup`, `_billing_status`, or `_billing_portal`.

*Call graph*: calls 4 internal fn (_billing_portal, _billing_setup, _billing_status, _owner_billing).


##### `_owner_billing`  (lines 547–558)

```
async def _owner_billing(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Checks that a billing tool call is allowed. Billing can only be managed by the workspace owner, speaking as themselves, in their own private conversation.

**Data flow**: It inspects the tool context for a speaking member, compares audience and speaker, asks the context whether the speaker is the owner, and returns the extension context if all checks pass; otherwise it raises an error before any provider call happens.

**Call relations**: `manage_billing` calls this before setup, status, or portal work. It is the gate that prevents non-owners or shared channels from triggering Stripe or Metronome changes.

*Call graph*: calls 1 internal fn (speaker_is_owner); called by 1 (manage_billing).


##### `_billing_setup`  (lines 561–595)

```
async def _billing_setup(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Starts billing setup for a workspace and returns a Stripe portal link for saving a payment method. It records the intended plan before handing the link to the owner.

**Data flow**: It reads any existing billing record. If none exists, it creates or reuses a Stripe Customer, builds a new billing record with the configured Metronome package and an hour-aligned contract start time, stores it, creates a Stripe portal session for payment-method update, logs setup, and returns the portal URL plus useful ids.

**Call relations**: `manage_billing` calls this for the `setup` action. It uses `_stripe_customer` for the Stripe Customer, `_portal_session` for the hosted link, `_billing_record` to avoid overwriting active progress, and `_text_result` for the response.

*Call graph*: calls 4 internal fn (_billing_record, _portal_session, _stripe_customer, _text_result); called by 1 (manage_billing); 3 external calls (__init__, now, log).


##### `_billing_status`  (lines 598–626)

```
async def _billing_status(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Reports what Stripe and Metronome currently say about the workspace’s billing. It does not trust only local stored flags.

**Data flow**: It reads the billing record. If none exists it returns `configured: false`; otherwise it asks Stripe whether a default payment method exists, asks Metronome whether the workspace’s own contract exists, and returns a JSON status including provider ids and whether the plan is active.

**Call relations**: `manage_billing` calls this for the `status` action. It uses `_has_default_payment_method`, `_contract_key`, `_contract_for`, and `_text_result`.

*Call graph*: calls 5 internal fn (_billing_record, _contract_for, _contract_key, _has_default_payment_method, _text_result); called by 1 (manage_billing).


##### `_billing_portal`  (lines 629–637)

```
async def _billing_portal(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Creates a fresh Stripe Customer Portal link for an already set-up workspace. The owner can use it for invoices, payment methods, and billing details.

**Data flow**: It reads the billing record, raises an error if setup has not happened yet, creates a portal session without narrowing it to a setup-only flow, and returns the URL as JSON text.

**Call relations**: `manage_billing` calls this for the `portal` action. It uses `_billing_record`, `_portal_session`, and `_text_result`.

*Call graph*: calls 3 internal fn (_billing_record, _portal_session, _text_result); called by 1 (manage_billing).


##### `_owner_seats`  (lines 640–645)

```
async def _owner_seats(ctx: ToolContext) -> Seats
```

**Purpose**: Checks that a seat-changing tool call is allowed. Only the workspace owner can grant or revoke seats.

**Data flow**: It inspects the tool context for a speaking member, asks whether that speaker is the owner, and returns a `Seats` service for the workspace if allowed; otherwise it raises an error.

**Call relations**: `grant_seat` and `revoke_seat` call this before making seat changes. It protects the core seat operations from unauthorized chat requests.

*Call graph*: calls 1 internal fn (speaker_is_owner); called by 2 (grant_seat, revoke_seat); 1 external calls (__init__).


##### `_snapshot_result`  (lines 648–662)

```
def _snapshot_result(snapshot: SeatSnapshot) -> ToolResult
```

**Purpose**: Formats a seat snapshot into the JSON text returned by seat tools. It makes the response easy for the agent to read and explain.

**Data flow**: It receives a `SeatSnapshot`, calculates billed overage seats from seated minus included seats, copies each member’s email, seated state, and owner flag, and wraps the payload as a tool result.

**Call relations**: Seat tools call this after reading or changing seats. It delegates the final wrapping to `_text_result`.

*Call graph*: calls 1 internal fn (_text_result); called by 3 (grant_seat, list_seats, revoke_seat).


##### `_text_result`  (lines 665–666)

```
def _text_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Wraps a small dictionary as a tool result containing JSON text. This gives chat tools a consistent response format.

**Data flow**: It receives a dictionary, serializes it with JSON, places that text inside a `TextContent`, and returns a `ToolResult` containing it.

**Call relations**: Billing and seat helper functions use this whenever they need to return structured information to the agent.

*Call graph*: called by 4 (_billing_portal, _billing_setup, _billing_status, _snapshot_result); 3 external calls (__init__, __init__, dumps).


##### `_require_env`  (lines 698–702)

```
def _require_env(name: str) -> str
```

**Purpose**: Reads a required environment variable and gives a clear error if it is missing. This prevents silent billing or shipping failures from bad deployment configuration.

**Data flow**: It receives an environment variable name, looks it up, returns the value if present, and raises a runtime error if absent or empty.

**Call relations**: Usage and seat shippers call this before posting to Metronome. Billing flows use `BillingConfig.from_env` instead because they need several settings at once.

*Call graph*: called by 2 (run, run).


##### `_stripe_customer`  (lines 705–722)

```
async def _stripe_customer(config: BillingConfig, workspace_id: UUID, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Creates or reuses the one Stripe Customer for a workspace. It uses a stable idempotency key so retrying the request does not create duplicate customers.

**Data flow**: It receives billing config, a workspace id, and an optional HTTP transport, posts customer details and workspace metadata to Stripe, validates that Stripe returned a non-empty customer id, and returns that id.

**Call relations**: `_billing_setup` calls this the first time billing is set up. It performs the provider request through `_stripe` and checks the returned id through `_as_str`.

*Call graph*: calls 2 internal fn (_as_str, _stripe); called by 1 (_billing_setup).


##### `_portal_session`  (lines 725–741)

```
async def _portal_session(config: BillingConfig, customer_id: str, flow: str | None, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Creates a short-lived Stripe Customer Portal URL. Depending on the flow, it can either focus the owner on saving a payment method or open the broader billing portal.

**Data flow**: It receives billing config, a Stripe customer id, an optional flow name, and an optional transport, posts a portal-session request to Stripe, validates the returned URL, and returns it.

**Call relations**: `_billing_setup` calls it for the payment-method update link, and `_billing_portal` calls it for the full portal. It uses `_stripe` for the HTTP request and `_as_str` to validate the URL.

*Call graph*: calls 2 internal fn (_as_str, _stripe); called by 2 (_billing_portal, _billing_setup).


##### `_has_default_payment_method`  (lines 744–754)

```
async def _has_default_payment_method(config: BillingConfig, customer_id: str, transport: httpx.AsyncBaseTransport | None) -> bool
```

**Purpose**: Checks whether Stripe says the customer has a default payment method saved. This is the gate that decides whether billing activation may proceed.

**Data flow**: It receives billing config, a Stripe customer id, and an optional transport, fetches the customer from Stripe, looks inside invoice settings, and returns true only when a default payment method string is present.

**Call relations**: Billing activation calls this before creating Metronome billing objects, and billing status calls it to report whether a card is on file. It performs the provider read through `_stripe`.

*Call graph*: calls 1 internal fn (_stripe); called by 2 (run, _billing_status).


##### `_stripe`  (lines 757–775)

```
async def _stripe(config: BillingConfig, method: str, path: str, transport: httpx.AsyncBaseTransport | None, data: dict[str, str] | None=None, idempotency_key: str | None=None) -> dict[str, object]
```

**Purpose**: Sends one HTTP request to Stripe and returns the JSON response. It centralizes Stripe authentication, API version pinning, timeout, idempotency headers, and error handling.

**Data flow**: It receives request details, builds headers with the secret key and fixed Stripe API version, optionally adds an idempotency key, sends the request with `httpx`, raises `StripeError` for non-success responses, and returns the decoded JSON body.

**Call relations**: Stripe-specific helpers call this for customer creation, portal sessions, and customer lookup. It is the low-level Stripe transport layer for the file.

*Call graph*: called by 3 (_has_default_payment_method, _portal_session, _stripe_customer); 2 external calls (__init__, AsyncClient).


##### `_metronome_customer`  (lines 778–823)

```
async def _metronome_customer(config: BillingConfig, alias: str, stripe_customer_id: str, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Finds or creates the workspace’s Metronome customer. The workspace UUID is used as the ingest alias so usage events attach to the right billable customer.

**Data flow**: It first searches Metronome for a customer with the workspace alias. If found, it returns that id. If not, it posts a customer create request with Stripe billing-provider configuration, handles alias conflicts by looking up the customer again, and returns the created or reconciled id.

**Call relations**: Billing activation calls this after Stripe has a default payment method. It uses `_customer_by_alias` for lookup and `_metronome` for provider calls.

*Call graph*: calls 2 internal fn (_customer_by_alias, _metronome); called by 1 (run); 1 external calls (__init__).


##### `_customer_by_alias`  (lines 826–835)

```
async def _customer_by_alias(config: BillingConfig, alias: str, transport: httpx.AsyncBaseTransport | None) -> str | None
```

**Purpose**: Looks up a Metronome customer by its ingest alias. This is how the extension recognizes the customer tied to a workspace’s usage events.

**Data flow**: It sends a Metronome customer list request filtered by ingest alias, returns the first customer id if one is present, and returns `None` otherwise.

**Call relations**: `_metronome_customer` uses this before creating a customer and again when reconciling a conflict. It performs the HTTP request through `_metronome`.

*Call graph*: calls 1 internal fn (_metronome); called by 1 (_metronome_customer).


##### `_metronome_contract`  (lines 838–874)

```
async def _metronome_contract(config: BillingConfig, customer_id: str, record: BillingRecord, uniqueness_key: str, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Finds or creates the workspace’s Metronome contract, which represents the live billing plan. It uses a permanent uniqueness key so retries and conflicts point back to the same contract.

**Data flow**: It first checks whether a matching contract already exists. If not, it posts a create request with the customer id, package alias, stored start time, and uniqueness key. If Metronome reports a conflict, it looks up the contract again. It returns the existing, created, or reconciled contract id.

**Call relations**: Billing activation calls this after the Metronome customer id is known. It uses `_contract_for` for lookup, `_rfc3339` for the contract start timestamp, and `_metronome` for provider calls.

*Call graph*: calls 3 internal fn (_contract_for, _metronome, _rfc3339); called by 1 (run); 1 external calls (__init__).


##### `_contract_for`  (lines 877–900)

```
async def _contract_for(config: BillingConfig, customer_id: str, uniqueness_key: str, transport: httpx.AsyncBaseTransport | None) -> str | None
```

**Purpose**: Finds this workspace’s own live Metronome contract on a customer. It matches by the extension’s uniqueness key rather than assuming any listed contract belongs to this workspace.

**Data flow**: It posts a contract list request for the customer, scans the returned contracts, returns the id whose uniqueness key matches, and returns `None` if no such contract is found.

**Call relations**: Billing status uses this to report whether the plan is active, and `_metronome_contract` uses it before and after create attempts. It calls `_metronome` for the provider request.

*Call graph*: calls 1 internal fn (_metronome); called by 2 (_billing_status, _metronome_contract).


##### `_metronome`  (lines 903–923)

```
async def _metronome(config: BillingConfig, method: str, path: str, transport: httpx.AsyncBaseTransport | None, body: dict[str, object] | None=None, params: dict[str, str] | None=None, idempotency_key
```

**Purpose**: Sends one HTTP request to Metronome and returns the JSON response. It centralizes Metronome authentication, timeout, conflict detection, and error reporting.

**Data flow**: It receives request details, builds an authorization header with the Metronome bearer token, optionally adds an idempotency key, sends the request with JSON body or query parameters, raises `MetronomeConflict` on HTTP 409, raises `MetronomeError` on other failures, and returns decoded JSON on success.

**Call relations**: Metronome customer and contract helpers call this for all non-ingest billing API requests. Usage and seat event ingestion use the separate `_ingest` helper.

*Call graph*: called by 4 (_contract_for, _customer_by_alias, _metronome_contract, _metronome_customer); 3 external calls (__init__, __init__, AsyncClient).


##### `_as_str`  (lines 926–930)

```
def _as_str(value: object, field: str) -> str
```

**Purpose**: Validates that a provider response field is a non-empty string. It turns malformed provider responses into clear local errors.

**Data flow**: It receives any value plus the field name being checked. If the value is a non-empty string it returns it; otherwise it raises a value error naming the missing field.

**Call relations**: `_stripe_customer` uses it to validate customer ids, and `_portal_session` uses it to validate portal URLs.

*Call graph*: called by 2 (_portal_session, _stripe_customer).


##### `_ingest`  (lines 933–941)

```
async def _ingest(token: str, events: list[dict[str, object]], transport: httpx.AsyncBaseTransport | None) -> None
```

**Purpose**: Posts usage or seat events to Metronome’s ingest endpoint. This is the sending path for metered events, separate from Metronome’s customer and contract APIs.

**Data flow**: It receives a bearer token, a list of event dictionaries, and an optional transport, posts the events to the ingest URL, and raises `MetronomeError` if Metronome does not accept them.

**Call relations**: `UsageShipper.run` calls this for usage batches, and `SeatShipper.run` calls it for daily seat snapshots.

*Call graph*: called by 2 (run, run); 2 external calls (__init__, AsyncClient).


##### `_rfc3339`  (lines 944–946)

```
def _rfc3339(moment: datetime) -> str
```

**Purpose**: Formats datetimes for provider APIs in an internet-standard timestamp style. If a time has no timezone, it treats it as UTC.

**Data flow**: It receives a `datetime`, ensures it has timezone information by adding UTC when missing, and returns its ISO/RFC3339-style string form.

**Call relations**: Usage event creation, seat event creation, and Metronome contract creation call this so outbound timestamps are consistent.

*Call graph*: called by 3 (_event, _events, _metronome_contract); 1 external calls (replace).


##### `manifest`  (lines 949–999)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the UFO host: its tools, scheduled jobs, prompt guidance, and credential slot. This is how the rest of the system discovers and runs the extension.

**Data flow**: It constructs tool definitions, job specifications with schedules and workspace candidate selectors, prompt sections for seats and billing behavior, and a credential slot for an Anthropic bring-your-own-key value, then returns a `Manifest` object.

**Call relations**: The extension loader calls this when registering the extension. The returned manifest connects chat tools to their handlers and scheduled jobs to `_ship`, `_ship_seats`, `_ask_seat_approvals`, and `_activate_billing`.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, metered_workspaces, member_workspaces).


### Scheduled task authoring
These files let users and workflows create recurring tasks, validate cron-like schedules, and pause work durably until a reply or timer resumes it.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/cron.py`

`domain_logic` · `schedule validation and task rescheduling`

The main task store only needs to know one thing: the next date and time a task should run. It does not understand cron, which is a compact text format for saying things like “run every day at 9:00” or “run every 5 minutes.” This file keeps that cron knowledge inside the scheduled-tasks extension, where it belongs.

It supports the common 5-field cron shape, usually meaning minute, hour, day of month, month, and day of week. First, `validate_cron` makes sure the text has exactly five parts and that the `croniter` library can understand it. This catches mistakes early, before a bad schedule is stored or used.

Then `next_fire` asks `croniter` for the next matching time after a given moment. An important detail is that it moves strictly forward. If a runner wakes up late, it does not try to create one run for every missed schedule slot. Instead, it finds the next single catch-up time. In everyday terms, it is like checking the next bus after you arrive at the stop, not trying to board every bus you already missed.

#### Function details

##### `validate_cron`  (lines 14–19)

```
def validate_cron(schedule: str) -> str
```

**Purpose**: This function checks whether a schedule string is a valid 5-field cron expression. It is used to reject malformed schedules before the system relies on them.

**Data flow**: It receives a schedule as text. It first splits the text into space-separated parts and confirms there are exactly five. Then it asks the external `croniter` library whether the expression is valid cron syntax. If either check fails, it raises a clear error; if both pass, it returns the original schedule unchanged.

**Call relations**: This is the gatekeeper before cron text is accepted by the scheduled-tasks extension. Inside the check, it hands the schedule to `croniter.croniter.is_valid` so the project does not have to reimplement all cron syntax rules itself.

*Call graph*: 1 external calls (is_valid).


##### `next_fire`  (lines 22–23)

```
def next_fire(schedule: str, after: datetime) -> datetime
```

**Purpose**: This function calculates the next time a cron-based task should run after a given moment. It lets the rest of the task system work with plain dates instead of understanding cron rules.

**Data flow**: It receives a cron schedule and an `after` datetime, meaning the point in time to move forward from. It gives both to `croniter`, which builds a small schedule calculator, and then asks that calculator for the next matching datetime. The result is returned as the next fire time; nothing else is changed.

**Call relations**: When the task system needs to update a task’s next run time, this function is the bridge from cron text to an actual timestamp. It delegates the detailed calendar calculation to `croniter.croniter`, then returns the computed datetime to whichever scheduling flow asked for it.

*Call graph*: 1 external calls (croniter).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/tools.py`

`domain_logic` · `object request handling and workflow pause/resume setup`

This file is the bridge between the agent’s normal tool/object world and the scheduling store that remembers future work. A scheduled task is treated like a workspace object: it has a name, a schedule, a prompt, an owner, details, and status. The file defines what a valid task looks like, checks that its cron schedule is valid, and saves it so that a later scheduled fire can re-enter the same conversation as the same agent. A cron schedule is a compact text pattern for recurring times, such as “9am every weekday.”

The main class, ScheduledTaskObjects, is the object-store adapter. Think of it like a clerk at a service desk: listing tasks, showing details, checking status, applying updates, and cancelling tasks, while enforcing who is allowed to do each action. Tasks are private to their creator, with the workspace owner also allowed to delete them.

The file also defines pause_and_wait, a normal tool rather than an object. It creates a one-time hidden scheduler row used internally by workflows. If no newer member message has already arrived, it arms a timer. If a message has already arrived, it skips the timer and tells the agent to finish so that the member message can resume the workflow.

#### Function details

##### `ScheduledTaskSpec.validate_utc_expiry`  (lines 65–68)

```
def validate_utc_expiry(cls, value: datetime | None) -> datetime | None
```

**Purpose**: This checks that a task expiry time, if one is supplied, is written as a UTC time. UTC is the shared world clock the scheduler uses, so accepting local or timezone-less times would make future cancellation ambiguous.

**Data flow**: It receives the proposed expires_at value from the task specification. If the value is missing, it lets it pass. If the value has no timezone or is not exactly UTC, it raises a validation error; otherwise it returns the same timestamp unchanged.

**Call relations**: This validator runs as part of building a ScheduledTaskSpec. Later, ScheduledTaskObjects._apply_owned can safely pass the expiry time to the scheduler knowing it is in the expected clock format.

*Call graph*: 2 external calls (utcoffset, timedelta).


##### `_require_scheduler`  (lines 85–88)

```
def _require_scheduler(ctx: ToolContext) -> ScheduleStore
```

**Purpose**: This is the safety check that finds the scheduling store in the current tool context. It prevents scheduled-task code from running in an environment where no scheduler is available.

**Data flow**: It receives the current ToolContext and looks inside its extension context for a scheduler. If the scheduler is present, it returns it. If not, it raises an error explaining that scheduled tasks require the scheduled-tasks extension and store.

**Call relations**: All code in this file that needs to read or write scheduled rows calls this first: listing, finding, status checks, creating, deleting, and pausing. It is the common doorway to ScheduleStore.

*Call graph*: called by 6 (_apply_owned, _delete_owned, _find, _owned_rows, _status, pause_and_wait).


##### `_summary`  (lines 91–92)

```
def _summary(task: ScheduledTask) -> str
```

**Purpose**: This makes a short human-readable label for a scheduled task in listings. It combines the schedule with the description, or with the prompt if no description was given.

**Data flow**: It receives a ScheduledTask row. It builds text in the form “schedule — description or prompt” and trims it to the maximum summary length so listings stay compact.

**Call relations**: ScheduledTaskObjects._owned_rows uses this when it turns raw scheduler rows into object-listing rows for the generic object system.

*Call graph*: called by 1 (_owned_rows).


##### `ScheduledTaskObjects._owned_rows`  (lines 113–121)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow, ...]
```

**Purpose**: This produces the list of scheduled tasks that the object system can show, including each task’s name, short summary, and owner. It is how scheduler rows become normal workspace objects in a listing.

**Data flow**: It receives the tool context, gets the scheduler through _require_scheduler, and asks the store for its visible scheduled tasks. For each task, it builds an OwnedRow with the task name, a compact summary from _summary, and an ObjectOwner based on the member who created it. It returns all of those rows as a tuple.

**Call relations**: The generic member-owned object machinery calls this when it needs rows for the scheduled_task object kind. It relies on _require_scheduler for storage access and _summary for display text.

*Call graph*: calls 2 internal fn (_require_scheduler, _summary); 2 external calls (__init__, __init__).


##### `ScheduledTaskObjects._detail`  (lines 123–142)

```
async def _detail(self, ctx: ToolContext, name: str) -> ObjectDetail[ScheduledTaskSpec] | None
```

**Purpose**: This returns the full object details for one scheduled task. It lets a caller see the saved schedule, prompt, description, expiry, timestamps, and the conversation where the task reports.

**Data flow**: It receives the tool context and a task name. It uses _find to look up the task. If there is no match, it returns nothing. If found, it builds a ScheduledTaskSpec from the stored task fields, adds creation and update times, and adds a link pointing to the conversation tied to the task.

**Call relations**: The object system uses this when someone asks to get a specific scheduled_task. It hands lookup work to ScheduledTaskObjects._find, then packages the result as an ObjectDetail for the broader object API.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `ScheduledTaskObjects._status`  (lines 144–168)

```
async def _status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: This reports runtime information about a scheduled task, such as when it will next run and what happened last time. It separates the task’s definition from its current operating state.

**Data flow**: It receives the tool context and task name, then asks the scheduler to inspect that task. If the scheduler has no such task, it returns nothing. Otherwise it builds a dictionary with next run time, last run time, expiry time, and, when available, a small excerpt of the last run’s response and turn status.

**Call relations**: The object system calls this when it needs status for a scheduled_task. It goes straight to the scheduler through _require_scheduler because status comes from scheduler inspection rather than from the object spec alone.

*Call graph*: calls 1 internal fn (_require_scheduler).


##### `ScheduledTaskObjects._apply_owned`  (lines 170–192)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ScheduledTaskSpec, old: ScheduledTaskSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: This creates or updates a scheduled task after checking ownership and validating the schedule. It is the point where a requested object change becomes a durable recurring job.

**Data flow**: It receives the current context, object name, new task spec, old spec if any, and owner information. It checks whether an existing creator is trying to edit their own task; if someone else tries, it raises an ownership error. It validates the cron text, calculates the next fire time from the current UTC time, and writes the task to the scheduler with the current conversation, agent, prompt, description, creator, and expiry.

**Call relations**: The object framework calls this when a scheduled_task manifest is applied. It uses _require_scheduler to reach the store, validate_cron to reject bad schedules, and next_fire to decide the first upcoming run.

*Call graph*: calls 1 internal fn (_require_scheduler); 4 external calls (__init__, now, next_fire, validate_cron).


##### `ScheduledTaskObjects._delete_owned`  (lines 194–195)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: This cancels a scheduled task that the ownership rules have allowed someone to delete. It removes the future recurring work from the scheduler.

**Data flow**: It receives the context, task name, and owner information. The owner information is not changed here; the surrounding object machinery has already used it for permission checks. The function gets the scheduler and asks it to cancel the named task.

**Call relations**: The object framework calls this after its member-owned deletion gate has accepted the request. It uses _require_scheduler, then hands the actual cancellation to ScheduleStore.

*Call graph*: calls 1 internal fn (_require_scheduler).


##### `ScheduledTaskObjects._find`  (lines 197–200)

```
async def _find(self, ctx: ToolContext, name: str) -> ScheduledTask | None
```

**Purpose**: This looks up one scheduled task by name from the scheduler’s list. It is a small helper used when full details are needed.

**Data flow**: It receives the context and a task name. It gets the scheduler, lists scheduled tasks, scans for the first task whose name matches, and returns that task. If none match, it returns nothing.

**Call relations**: ScheduledTaskObjects._detail calls this before building an ObjectDetail. It keeps the name-searching logic in one place instead of repeating it inside the detail builder.

*Call graph*: calls 1 internal fn (_require_scheduler); called by 1 (_detail).


##### `pause_and_wait`  (lines 230–267)

```
async def pause_and_wait(ctx: ToolContext, args: PauseAndWaitInput) -> ToolResult
```

**Purpose**: This tool pauses the current workflow until either a new member message arrives or a durable timer expires. It is useful for real-world waits such as approvals, verification emails, or cooldown periods where the agent must stop now and resume later with clear instructions.

**Data flow**: It receives the tool context and PauseAndWaitInput containing the message to show now, wait length, reason, next steps, and optional metadata. It calculates a resume time, builds a hidden resume prompt, and asks the scheduler to create a one-time pause row tied to the current conversation and turn. If a newer member message has already been admitted, it returns instructions saying the member message will resume the workflow. Otherwise it returns instructions saying a timer is armed, including the resume time and the state needed later.

**Call relations**: The tool system invokes this when the agent calls the pause_and_wait tool. It uses _require_scheduler to reach durable scheduling, then returns a ToolResult containing TextContent that tells the agent exactly how to end its turn while preserving the resume instructions.

*Call graph*: calls 1 internal fn (_require_scheduler); 5 external calls (__init__, __init__, now, timedelta, dumps).

## 📊 State Registers Touched

- `reg-effective-config` — The running service’s merged settings, such as required keys, enabled backends, safety options, and service behavior.
- `reg-extension-pack-manifest` — The installed pack and extension menu that says what tools, routes, jobs, skills, credentials, and backends exist.
- `reg-workspace-tenant-record` — The customer workspace record that all users, conversations, data, tools, and billing are kept under.
- `reg-member-session-auth` — The signed-in person’s identity and session proof used to decide who is making a request.
- `reg-agent-profile-settings` — The saved assistant settings for a workspace, including which agent is used and what it is allowed to do.
- `reg-conversation-thread-state` — The saved conversation identity and history that let the system continue the same thread over time.
- `reg-turn-record-lifecycle` — The durable record of each unit of agent work, including who started it, its status, parent links, and final result.
- `reg-durable-work-queue` — The shared queue of conversation and job work waiting to be claimed, retried, resumed, or completed by workers.
- `reg-runtime-presence` — The live roll-call of server and worker processes used to recover abandoned work safely.
- `reg-cancellation-state` — The shared stop signal and saved cancellation status for turns, child turns, and paused work.
- `reg-seat-entitlement` — The workspace membership and seat-limit state that decides which people the agent may serve.
- `reg-spend-ledger-billing` — The shared usage ledger, prices, caps, exports, and billing records used to track and limit spending.
- `reg-model-catalog-pricing` — The shared list of available AI models, provider details, limits, credentials, and prices.
- `reg-prompt-skill-library` — The enabled instructions, skill folders, helper profiles, and prompt versions that shape how the agent behaves.
- `reg-source-page-sync-state` — The saved sources, pages, sync cursors, deletion markers, and retry state for imported external content.
- `reg-search-index-memory-graph` — The shared recall stores for searchable chunks, remembered facts, memory pages, and knowledge-graph links.
- `reg-scheduled-task-state` — The saved clock-based tasks, waits, pauses, last-run markers, and expiration times used to wake work later.
- `reg-extension-object-store` — The durable per-workspace storage and named objects that extensions expose or update over time.
- `reg-transcript-compaction-store` — The saved conversation transcript and compacted summaries used to rebuild context and inspect past turns.
- `reg-self-improvement-governance-state` — Saved failure corpora, replay/evaluation results, prompt-change proposals, approvals, and guards for the self-improvement loop.
- `reg-alert-watch-state` — Saved alert subscriptions, match rules, checkpoints, and pending notifications used to react to changed synced pages.
- `reg-evaluation-fixture-state` — Durable fake inbox, calendar, code-search, and other test-fixture records used by evaluation jobs without touching real providers.
- `reg-human-approval-proposal-state` — Durable proposal records for changes or actions that must be reviewed, accepted, rejected, or superseded before taking effect.
