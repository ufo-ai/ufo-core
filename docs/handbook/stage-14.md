# Scheduled jobs, billing operations, and self-improvement loops  `stage-14`

This stage is behind-the-scenes machinery. It runs work that should happen later or on a schedule, rather than during one user request. The shared job engine finds background jobs at startup, chooses which workspaces need them, and runs each job inside the right workspace. A candidate finder safely looks across workspaces only to collect IDs, so private work stays scoped.

The scheduling pieces act like an alarm clock. Agents can create repeating tasks or “wait until” pauses. A cron parser checks timing rules and computes the next run. A runner wakes up, claims due tasks so they do not fire twice, invokes them, records results, and reschedules repeats.

Other jobs handle business operations. The Metronome extension sends usage and seat data to billing systems and connects Stripe payment setup to chat tools. A Slack Connect job turns completed signups into one invitation without slowing signup.

The self-improvement loop mines past failures into a test corpus, asks a model for prompt rewrites, replays old tasks without re-running real tools, evaluates old versus new prompts, applies safety gates, and only then opens governed proposals. Governance applies changes only if the original prompt has not changed meanwhile.

## Files in this stage

### Prompt self-improvement loop
Builds, tests, gates, and governs proposed prompt changes using archived work, replayed tasks, model-generated candidates, and proposal safeguards.

### `extensions/self_improvement/ufo_ext_self_improvement/evaluation.py`

`domain_logic` · `self-improvement evaluation phase`

This file is the evidence-gathering part of the self-improvement system. When the system has a candidate prompt, it cannot just trust that the prompt sounds better. It must test it on real saved examples and check whether it improves results without hurting other kinds of tasks.

The main object, CandidateEvaluation, compares two “arms”: the current prompt and the candidate prompt. For each held-out task, it replays the agent using the same saved messages and archived tool behavior, so the prompt is meant to be the only meaningful difference. This is like testing two recipes with the same ingredients and oven, changing only one instruction.

Each replay produces a final answer. The file then asks a judge model to decide whether that answer satisfies the original request. The judge must return a small JSON object saying whether the answer was accepted. If the judge response is missing valid JSON, the answer is treated as not accepted, which is a conservative choice.

After collecting pass/fail labels for local tasks and global tasks, this file calls the two-stage gate. The local check asks, “Did the candidate improve the task class it was meant to improve?” The global check asks, “Did it avoid making other task classes worse?” Without this file, prompt changes could be accepted based on guesswork instead of controlled evidence.

#### Function details

##### `CandidateEvaluation.evaluate`  (lines 24–33)

```
async def evaluate(self, candidate_prompt: str, current_prompt: str, local_held_out: tuple[TaskExample, ...], global_held_out: tuple[TaskExample, ...]=()) -> GateVerdict
```

**Purpose**: This is the top-level test for a candidate prompt. It compares the candidate prompt against the current prompt on local held-out tasks and optional global held-out tasks, then returns the gate’s final verdict.

**Data flow**: It receives the candidate prompt, the current prompt, a set of local examples, and optionally a set of broader global examples. It asks _labels to turn each example into pass/fail results for both prompts. It then gives those labels to the two-stage gate, which returns whether the candidate should pass or fail.

**Call relations**: This method starts the evaluation flow for the file. It relies on _labels to collect the raw evidence, then hands that evidence to two_stage_gate so the broader acceptance rules can decide whether the prompt change is safe and useful.

*Call graph*: calls 1 internal fn (_labels); 1 external calls (two_stage_gate).


##### `CandidateEvaluation._labels`  (lines 35–45)

```
async def _labels(self, candidate_prompt: str, current_prompt: str, held_out: tuple[TaskExample, ...]) -> tuple[OutcomeLabel, ...]
```

**Purpose**: This function runs the side-by-side prompt comparison for a group of saved examples. For every task, it tests both the current prompt and the candidate prompt, then records whether each answer was accepted.

**Data flow**: It receives both prompts and a tuple of held-out task examples. It creates a ReplayEvaluation object, replays each example once with the current prompt and once with the candidate prompt, asks _accepts to judge each final answer, and turns each result into an OutcomeLabel showing which prompt was used and whether it succeeded. It returns all labels as an immutable tuple.

**Call relations**: It is called by evaluate when local or global evidence is needed. Inside the loop, it uses ReplayEvaluation to regenerate answers under controlled conditions, calls _accepts to score those answers, and packages the scores as OutcomeLabel objects for the gate.

*Call graph*: calls 1 internal fn (_accepts); called by 1 (evaluate); 2 external calls (__init__, __init__).


##### `CandidateEvaluation._accepts`  (lines 47–59)

```
async def _accepts(self, request: str, answer: str) -> bool
```

**Purpose**: This function asks the judge model whether one generated answer satisfies the original user request. It turns the judge’s response into a simple true or false result.

**Data flow**: It receives the original request and the answer being judged. It sends both to the judge model with instructions to return only JSON, then looks for a JSON object in the judge’s text. If the JSON can be parsed and contains accepted set to true, it returns true; otherwise, including malformed output, it returns false.

**Call relations**: It is called by _labels after each replay produces an answer. It creates the user message sent to the judge model and uses json.loads to read the judge’s JSON response, giving _labels the clean pass/fail value needed for comparison.

*Call graph*: called by 1 (_labels); 2 external calls (__init__, loads).


### `core/src/ufo/governance.py`

`domain_logic` · `agent configuration change and approval`

This file is a safety gate for changing an agent’s configuration, specifically its prompt. Instead of letting code overwrite an agent prompt directly, it creates a proposal that says: “change this prompt from version A to version B.” The important trick is the digest, which is a short fingerprint made from the prompt text. Like checking a package seal before opening it, the system checks that the current prompt still matches the fingerprint recorded when the proposal was made.

The main class, Governance, works inside one workspace. When someone proposes a change, it first confirms the target agent really belongs to that workspace. Then it stores a pending proposal with the old prompt fingerprint, the new prompt text, and the new prompt fingerprint.

Later, approval is cautious. It loads the proposal, checks that it is still pending, then locks and reads the current agent prompt from the database. If the prompt has changed since the proposal was opened, the proposal is rejected instead of being applied. If it still matches, the prompt is updated and the proposal is marked approved. This prevents stale approvals from accidentally overwriting newer work.

#### Function details

##### `prompt_digest`  (lines 16–17)

```
def prompt_digest(prompt: str) -> str
```

**Purpose**: This turns a prompt into a stable fingerprint using SHA-256, a standard one-way hashing method. The fingerprint lets the system compare prompt versions without relying on the full text each time.

**Data flow**: It receives prompt text as input. It encodes that text, runs it through SHA-256, and returns the resulting hexadecimal fingerprint string. It does not change anything outside itself.

**Call relations**: When a change is proposed, Governance.propose_change uses this to record the fingerprint of the new prompt. When a proposal is approved, Governance.approve_proposal uses it to check whether the current prompt still matches the original fingerprint saved in the proposal.

*Call graph*: called by 2 (approve_proposal, propose_change); 1 external calls (sha256).


##### `Governance.propose_change`  (lines 28–55)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: This opens a new proposed prompt change for an agent in the current workspace. It records what prompt version the change expects to replace, and what new prompt should be used if the proposal is later approved.

**Data flow**: It receives an AgentChange containing the target agent, the expected old prompt fingerprint, and the new prompt text. It creates a new proposal ID, checks the database to make sure the agent exists in this workspace, computes the new prompt’s fingerprint, and inserts a pending proposal row. It returns a ProposalRef containing the new proposal ID. If the agent is not in the workspace, it raises an error instead of creating anything.

**Call relations**: This is used at the start of the governance flow, when code wants to request a prompt change rather than apply it directly. It calls prompt_digest to fingerprint the proposed new prompt, uses a workspace database transaction so the checks and insert happen safely together, and returns a reference that later approval code can use.

*Call graph*: calls 1 internal fn (prompt_digest); 5 external calls (__init__, insert, select, workspace_tx, uuid4).


##### `Governance.approve_proposal`  (lines 57–114)

```
async def approve_proposal(self, proposal_id: UUID) -> None
```

**Purpose**: This attempts to approve and apply a pending proposal. It only changes the agent prompt if the prompt has not changed since the proposal was created.

**Data flow**: It receives a proposal ID. It loads the proposal for this workspace, confirms it exists and is still pending, then reads and locks the current agent prompt so another update cannot race with it. It compares the current prompt’s fingerprint with the proposal’s saved starting fingerprint. If they differ, it marks the proposal rejected and logs that result. If they match, it updates the agent prompt, marks the proposal approved, and logs the approval.

**Call relations**: This is the second half of the governance flow, after Governance.propose_change has created a pending proposal. It relies on prompt_digest for the safety comparison, uses a workspace database transaction to keep the decision and update together, and sends outcome messages to the logging system so approvals and rejections can be observed later.

*Call graph*: calls 1 internal fn (prompt_digest); 4 external calls (select, update, workspace_tx, log).


### `extensions/self_improvement/ufo_ext_self_improvement/corpus.py`

`domain_logic` · `self-improvement corpus building`

This file helps the self-improvement loop find useful training and testing material from the workspace’s history. The basic idea is simple: if a past conversation contains a tool error, that error is a clear sign of friction. Since the system does not have a separate “I struggled here” signal, tool failures become the clue for what should be improved.

A single flagged conversation becomes a TaskExample. That example keeps the conversation id, the user’s original request, the full message history, and a short description of the tool problem. Examples are grouped into TaskClass objects, where each class represents one failing tool, named like “tool:search” or “tool:browser”.

The important safety feature is the split between “mine” and “held_out”. The “mine” set is what a proposer may study when suggesting an improvement. The “held_out” set is saved for later replay, like an exam the proposal has not already seen. This prevents the system from grading a candidate on the same conversations it learned from.

The file also enforces small limits: a class must have at least enough examples to place one in each side of the split. Classes are sorted so the largest, most useful groups appear first.

#### Function details

##### `first_request`  (lines 39–43)

```
def first_request(messages: tuple[Message, ...]) -> str | None
```

**Purpose**: Finds the first real user request in a conversation. This gives the self-improvement loop a plain grading target: what the user originally wanted.

**Data flow**: It receives the conversation messages. It scans them from the start, looking for the first message whose role is user and whose content is non-empty text. It returns that text, or returns nothing if no usable user request is found.

**Call relations**: When bad_trajectory is deciding whether a conversation can become an example, it calls first_request to get the user request. If this function cannot find one, the conversation is skipped because there is no clear task to judge against.

*Call graph*: called by 1 (bad_trajectory).


##### `first_tool_error`  (lines 46–65)

```
def first_tool_error(messages: tuple[Message, ...]) -> tuple[str, str] | None
```

**Purpose**: Finds the first tool failure recorded in a conversation and identifies which tool failed. This is the main signal that a trajectory is worth studying.

**Data flow**: It receives the conversation messages. First it reads tool-use blocks and remembers which tool name belongs to each tool-use id. Then it scans for the first tool-result block marked as an error. If it can match that failed result back to a tool name, it returns the tool name and the error text. If no matched tool error exists, it returns nothing.

**Call relations**: bad_trajectory calls this function before making an example. The result tells bad_trajectory both whether the conversation is useful and which tool-based class the example belongs to.

*Call graph*: called by 1 (bad_trajectory).


##### `bad_trajectory`  (lines 68–78)

```
def bad_trajectory(trajectory: Trajectory) -> tuple[str, TaskExample] | None
```

**Purpose**: Turns one past conversation into a self-improvement example, but only if it contains both a user request and a tool error. It also decides the example’s class based on the failed tool.

**Data flow**: It receives one trajectory, which includes the conversation id and messages. It asks first_tool_error for the first failed tool round and first_request for the original user request. If either is missing, it returns nothing. Otherwise it builds a problem sentence such as “the X tool errored: ...”, packages the useful details into a TaskExample, and returns that example together with a class name like “tool:X”.

**Call relations**: task_classes calls bad_trajectory for every trajectory it is given. bad_trajectory is the filter in the pipeline: it passes along only conversations that have enough information to be useful for proposing and grading improvements.

*Call graph*: calls 2 internal fn (first_request, first_tool_error); called by 1 (task_classes); 1 external calls (__init__).


##### `task_classes`  (lines 81–94)

```
def task_classes(trajectories: tuple[Trajectory, ...]) -> tuple[TaskClass, ...]
```

**Purpose**: Builds the final set of task classes from many past trajectories. It groups examples by the tool that failed and keeps only groups large enough to be split fairly into learning and testing examples.

**Data flow**: It receives a tuple of trajectories. For each one, it calls bad_trajectory; skipped trajectories disappear from consideration. The remaining examples are collected by class name. Each class group is then passed to _split, which either returns a usable TaskClass or rejects the group for being too small. The final classes are sorted with larger classes first, and returned as a tuple.

**Call relations**: This is the main entry point within this file’s logic. It coordinates the smaller helpers: bad_trajectory extracts useful examples, and _split turns each group into separate mine and held-out sets.

*Call graph*: calls 2 internal fn (_split, bad_trajectory).


##### `_split`  (lines 97–102)

```
def _split(name: str, examples: tuple[TaskExample, ...]) -> TaskClass | None
```

**Purpose**: Splits one group of examples into a held-out test set and a mine set for learning. This protects evaluation from using the exact same examples that inspired a proposed improvement.

**Data flow**: It receives a class name and that class’s examples. If there are too few examples to put at least one on each side, it returns nothing. Otherwise it sorts examples by conversation id for a stable, repeatable order, chooses an evaluation count, puts the first part into held_out, puts the rest into mine, and returns a TaskClass.

**Call relations**: task_classes calls _split after grouping examples by failed tool. _split is the final gate: it decides whether a tool failure class has enough evidence to be useful, and if so hands back a ready-to-use TaskClass.

*Call graph*: called by 1 (task_classes); 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/cron.py`

`orchestration` · `scheduled cron tick`

This file is the careful “heartbeat” of the self-improvement extension. On each scheduled tick, it reviews the workspace’s recorded agent trajectories, meaning past conversations or task attempts, grouped by agent. For each agent, it may open one candidate improved prompt for the agent’s current prompt digest, which is a fingerprint of the exact prompt version being targeted.

The important safety idea is that this code never directly changes an agent. It only asks for a governed proposal after a candidate has passed evaluation enough times in a row. Think of it like a probation period: a new prompt must keep passing tests over multiple checks before it is sent to people or governance for approval.

The file stores each candidate’s state in the extension’s scoped store. That saved state records the prompt it came from, the proposed new prompt, which held-out examples are used to test it, how many times it has passed, and whether it was rejected or promoted. If a candidate was already rejected or promoted for the same prompt digest, the code will not propose it again until the agent’s prompt digest changes through an approved update. This prevents the system from endlessly re-opening the same idea.

#### Function details

##### `ImproveCron.run`  (lines 50–52)

```
async def run(self) -> None
```

**Purpose**: This is the top-level scheduled action. It gathers all known trajectories, groups them by agent, and advances the self-improvement process separately for each agent.

**Data flow**: It starts by asking the extension context for all trajectories. It groups those records by agent ID, then sends each agent’s group of trajectories into the next step. It does not return a value; its effect is to move candidate prompt evaluations forward and possibly create governed proposals.

**Call relations**: The cron runner calls this method when the scheduled self-improvement tick fires. Inside, it relies on _by_agent to sort trajectories into per-agent bundles, then calls ImproveCron._advance once for each bundle.

*Call graph*: calls 2 internal fn (_advance, _by_agent).


##### `ImproveCron._advance`  (lines 54–60)

```
async def _advance(self, agent_id: UUID, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: This moves one agent forward by one step in the improvement process. It either finds or opens a candidate for the agent’s current prompt version, then tests that candidate if one exists.

**Data flow**: It receives an agent ID and that agent’s trajectories. It reads the current prompt digest from the first trajectory, builds the storage key for this agent’s candidate, and asks _active_or_open for a candidate. If there is no candidate, it stops. If there is one, it passes the candidate into _gate for evaluation.

**Call relations**: ImproveCron.run calls this after grouping the trajectories. This method is the bridge between candidate setup, done by ImproveCron._active_or_open, and candidate testing, done by ImproveCron._gate.

*Call graph*: calls 2 internal fn (_active_or_open, _gate); called by 1 (run).


##### `ImproveCron._active_or_open`  (lines 62–83)

```
async def _active_or_open(self, key: str, from_digest: str, trajectories: tuple[Trajectory, ...]) -> CandidateState | None
```

**Purpose**: This finds the still-active candidate for a prompt version, or creates a new one if it is safe and useful to do so. It also prevents already resolved candidates from being proposed again for the same prompt digest.

**Data flow**: It receives a storage key, the current prompt digest, and the agent’s trajectories. First it checks the scoped store for a saved candidate. If the saved candidate belongs to this same prompt digest and is still evaluating, it returns that candidate. If the saved candidate was already promoted or rejected, it returns nothing. If there is no usable saved candidate, it looks for task classes in the trajectories, asks the prompt proposer for a new prompt based on the current prompt and one task class, saves the opened candidate, and returns it.

**Call relations**: ImproveCron._advance calls this before any evaluation happens. This function calls task_classes to find usable task groups, and it uses the proposer to create a candidate prompt when none is already active.

*Call graph*: called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._gate`  (lines 85–117)

```
async def _gate(self, agent_id: UUID, key: str, from_digest: str, candidate: CandidateState, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: This is the safety gate for a candidate prompt. It evaluates whether the proposed prompt is good enough, counts consecutive passing ticks, rejects failures, and opens a governed change proposal only after enough repeated passes.

**Data flow**: It receives the agent, storage key, current prompt digest, candidate state, and trajectories. It builds two test sets: the candidate’s own held-out examples and held-out examples from other task classes. It sends the proposed prompt, the old prompt, and those examples to the evaluator. If the candidate fails, it saves it as rejected with zero passes. If it passes but has not yet reached the required stability count, it saves the increased pass count and keeps evaluating. If it reaches the required count, it asks the extension context to propose an AgentChange and then saves the candidate as promoted with the proposal ID.

**Call relations**: ImproveCron._advance calls this after a candidate is available. This function calls _held_out to rebuild the candidate’s saved test examples, calls task_classes to gather broader comparison examples, uses _save to persist each outcome, and creates an AgentChange only when the candidate has passed the gate enough times.

*Call graph*: calls 2 internal fn (_save, _held_out); called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._save`  (lines 119–131)

```
async def _save(self, key: str, candidate: CandidateState, *, status: CandidateStatus, gate_passes: int, proposal_id: str | None=None) -> None
```

**Purpose**: This writes an updated candidate state back to the extension’s store. It keeps the existing candidate details but changes the status, pass count, and optional proposal ID.

**Data flow**: It receives the storage key, the existing candidate, and the new status information. It copies the candidate with the updated fields, turns it into JSON-friendly data, and writes it to the scoped store. It returns nothing, but the stored candidate state changes.

**Call relations**: ImproveCron._gate calls this whenever evaluation changes a candidate’s state. It is the small persistence step that makes future cron ticks remember whether a candidate is still being tested, rejected, or already promoted.

*Call graph*: called by 1 (_gate); 1 external calls (model_copy).


##### `_by_agent`  (lines 134–138)

```
def _by_agent(trajectories: tuple[Trajectory, ...]) -> Mapping[UUID, tuple[Trajectory, ...]]
```

**Purpose**: This groups trajectory records by the agent that produced them. It lets the cron process each agent independently instead of mixing all past work together.

**Data flow**: It receives a tuple of trajectories. It reads each trajectory’s agent ID, collects trajectories with the same agent ID into a group, and returns a mapping from agent ID to that agent’s tuple of trajectories.

**Call relations**: ImproveCron.run calls this immediately after fetching trajectories. The grouped output drives the rest of the cron flow, because ImproveCron._advance expects to work on one agent at a time.

*Call graph*: called by 1 (run).


##### `_held_out`  (lines 141–153)

```
def _held_out(trajectories: tuple[Trajectory, ...], held_out: tuple[str, ...]) -> tuple[TaskExample, ...]
```

**Purpose**: This reconstructs the candidate’s saved held-out test examples from the current trajectories. It keeps only examples that are still present and are considered bad trajectories, because those are the examples used to judge whether the new prompt improves problem cases.

**Data flow**: It receives all trajectories for an agent and a tuple of saved conversation IDs. It builds a lookup table from conversation ID to trajectory, then walks through the saved IDs. For each matching trajectory, it asks bad_trajectory whether that trajectory should be turned into a task example. Matching flagged examples are collected and returned as a tuple.

**Call relations**: ImproveCron._gate calls this before evaluation so the evaluator gets the candidate’s own held-out examples. This function delegates the judgment of whether a trajectory is a useful bad example to bad_trajectory.

*Call graph*: called by 1 (_gate); 1 external calls (bad_trajectory).


### `extensions/self_improvement/ufo_ext_self_improvement/gate.py`

`domain_logic` · `self-improvement evaluation`

This file is the safety gate for self-improvement. When the system tries a candidate prompt, it does not promote it just because it won a few examples by luck. Instead, it asks: did the candidate improve acceptance often enough, with enough evidence, and did it avoid hurting other task types?

The file treats each replayed example as belonging to one of two arms: the candidate prompt was present, or it was absent. It then counts successes and totals for both arms. From those counts it estimates the “lift,” meaning how much better the candidate did than the current prompt.

Because small samples can be misleading, the code uses confidence bounds. A confidence bound is a cautious estimate that says, in effect, “even after allowing for uncertainty, the improvement is at least this much.” The local gate only passes if this cautious lower estimate clears a required floor and both arms have enough examples.

There is also a second, broader check. A candidate may be great for one mined task class but harmful to other tasks. The global check blocks only when there is confident evidence of regression. If the broader evidence is too thin or merely noisy, it does not block promotion. In short, this file is like a quality-control inspector: reward real wins, ignore lucky noise, and stop changes that clearly break other work.

#### Function details

##### `wilson_lower_bound`  (lines 53–60)

```
def wilson_lower_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: This function calculates a cautious lower estimate for a success rate. It answers: given some accepted examples out of a total, what success rate can we reasonably trust as a lower limit?

**Data flow**: It receives a number of accepted examples, a total number of examples, and an optional confidence setting. If there are no examples, it returns 0. Otherwise it computes a Wilson confidence bound, which adjusts the raw success rate downward to account for uncertainty, and returns a value between 0 and 1.

**Call relations**: This is a building block for comparing prompt versions. The lift calculations call it when they need the pessimistic side of a success rate before deciding whether a candidate truly improved or whether the result may be noise.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `wilson_upper_bound`  (lines 63–70)

```
def wilson_upper_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: This function calculates a cautious upper estimate for a success rate. It answers: after allowing for uncertainty, how high could this success rate reasonably be?

**Data flow**: It receives accepted examples, total examples, and an optional confidence setting. If there are no examples, it returns 1, meaning the rate is completely unconstrained. Otherwise it computes the upper Wilson confidence bound and returns a value between 0 and 1.

**Call relations**: The lift calculations use this alongside the lower bound. It helps measure the uncertainty around each prompt arm so the gate can avoid promoting changes based on fragile evidence.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `lift_lower_bound`  (lines 73–84)

```
def lift_lower_bound(cont: Contingency) -> float
```

**Purpose**: This function computes the cautious lower bound of the candidate prompt’s improvement over the current prompt. It is the key local evidence measure used to decide whether a candidate really looks better.

**Data flow**: It receives a Contingency count: accepted and total examples for the candidate-present arm and the candidate-absent arm. If either arm has no examples, it returns 0. Otherwise it compares the two raw acceptance rates, subtracts an uncertainty penalty built from Wilson bounds, and returns the cautious lower estimate of the lift.

**Call relations**: score_gate calls this after the replay labels have been counted. It relies on wilson_lower_bound and wilson_upper_bound to account for uncertainty in both arms before the local promotion decision is made.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (score_gate); 1 external calls (sqrt).


##### `lift_upper_bound`  (lines 87–98)

```
def lift_upper_bound(cont: Contingency) -> float
```

**Purpose**: This function computes the optimistic upper bound of the candidate prompt’s lift. It is mainly used to decide whether a candidate is clearly harmful on the broader task set.

**Data flow**: It receives a Contingency count for candidate-present and candidate-absent examples. If either side is empty, it returns 0. Otherwise it compares the raw rates and adds an uncertainty allowance, producing the best plausible lift after accounting for uncertainty.

**Call relations**: global_non_inferior calls this during the broader safety check. If even this optimistic estimate is worse than the allowed regression margin, the candidate is treated as confidently harmful.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (global_non_inferior); 1 external calls (sqrt).


##### `contingency`  (lines 101–109)

```
def contingency(labels: tuple[OutcomeLabel, ...]) -> Contingency
```

**Purpose**: This function turns individual replay results into the four counts needed for statistical comparison. It separates examples where the candidate prompt was present from examples where it was absent, then counts successes in each group.

**Data flow**: It receives a tuple of OutcomeLabel records. Each record says whether the candidate was present and whether the answer succeeded. The function splits the labels into present and absent groups, counts accepted examples and totals for each, and returns a Contingency object containing those four numbers.

**Call relations**: Both score_gate and global_non_inferior call this before doing any lift math. It is the small counting step that converts raw replay outcomes into the shape required by the statistical gate.

*Call graph*: called by 2 (global_non_inferior, score_gate); 1 external calls (__init__).


##### `score_gate`  (lines 112–139)

```
def score_gate(labels: tuple[OutcomeLabel, ...], lower_bound: float=LIFT_LOWER_BOUND, n_floor: int=N_FLOOR) -> GateVerdict
```

**Purpose**: This function makes the local promotion decision for the task class where the candidate was discovered. It passes only when there are enough examples on both sides and the cautious lift estimate is above the required improvement floor.

**Data flow**: It receives replay labels, plus optional thresholds for minimum lift and minimum examples per arm. It counts the outcomes with contingency, computes the lower lift bound, checks whether both arms have enough data, then checks whether the lift is strong enough. It returns a GateVerdict saying pass or fail, with a human-readable reason and the evidence counts.

**Call relations**: two_stage_gate calls this first. If score_gate fails, the full two-stage process stops immediately because there is no proven local win to promote.

*Call graph*: calls 2 internal fn (contingency, lift_lower_bound); called by 1 (two_stage_gate); 1 external calls (__init__).


##### `global_non_inferior`  (lines 142–154)

```
def global_non_inferior(labels: tuple[OutcomeLabel, ...], margin: float=GLOBAL_REGRESSION_MARGIN, n_floor: int=N_FLOOR) -> bool
```

**Purpose**: This function checks whether the candidate avoids clearly hurting other task classes. It is deliberately permissive when evidence is scarce: it blocks only when the data confidently shows meaningful regression.

**Data flow**: It receives replay labels from the broader global set, plus optional settings for allowed regression margin and minimum examples. It counts the outcomes. If either arm has too few examples, it returns true. Otherwise it computes the optimistic lift bound and returns true unless even that optimistic bound is below the allowed negative margin.

**Call relations**: two_stage_gate calls this only after the local gate has passed. It uses contingency and lift_upper_bound to decide whether a locally good prompt should still be rejected because it damages other work.

*Call graph*: calls 2 internal fn (contingency, lift_upper_bound); called by 1 (two_stage_gate).


##### `two_stage_gate`  (lines 157–174)

```
def two_stage_gate(local_labels: tuple[OutcomeLabel, ...], global_labels: tuple[OutcomeLabel, ...]) -> GateVerdict
```

**Purpose**: This function gives the final promotion verdict. It requires both a proven local improvement and no confident global regression.

**Data flow**: It receives two sets of replay labels: local labels for the task class being improved, and global labels for other task classes. First it asks score_gate for the local verdict. If that fails, it returns that failure. If the local check passes, it asks global_non_inferior whether the broader behavior is safe. If the global check fails, it returns a failing GateVerdict with a regression reason. Otherwise it returns the successful local verdict.

**Call relations**: This is the top-level decision function in the file. It ties together the local evidence check and the broader safety check, using score_gate first and global_non_inferior second so that only locally promising candidates face the global regression screen.

*Call graph*: calls 2 internal fn (global_non_inferior, score_gate); 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/model.py`

`io_transport` · `cross-cutting during proposal, replay, and grading model calls`

The self-improvement extension needs to call a language model in several places, such as proposing changes, replaying past work, and grading results. Without this file, each part of the extension would have to know the details of the SDK’s model request format, token limit, and settings. That would make model use harder to change and easier to get inconsistent.

This file defines two small promises, called protocols: `ModelLeg` means “something that can complete a text prompt,” and `ReplayLeg` means “something that can produce the next message when tools are available.” A protocol is like saying, “I do not care what brand of plug this is, as long as it fits this socket.”

`ModelAccessLeg` is the real adapter. It wraps the SDK’s `ModelAccess`, which is the project’s metered, workspace-aware access point to the model. “Metered” means calls can be tracked or limited, usually because model use has cost. Both model calls use the same maximum output size, 2048 tokens, and turn model reasoning off. The result is a narrow, predictable model interface that the rest of the extension can depend on.

#### Function details

##### `ModelLeg.complete`  (lines 13–13)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This describes the simplest kind of model call the extension needs: give the model instructions and conversation messages, and get back plain text. It is a promise that any compatible model adapter must fulfill.

**Data flow**: It receives a system instruction and a tuple of conversation messages. A concrete implementation will send those to a model and return the model’s text answer. This protocol method itself does not do the work; it defines the shape of the work.

**Call relations**: Other code can depend on `ModelLeg.complete` when it only needs text from a model and does not want to know which model adapter is underneath. `ModelAccessLeg.complete` is the concrete implementation in this file that satisfies that promise.


##### `ReplayLeg.turn`  (lines 17–19)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This describes a model call for replay-style work, where the model may be given tools it can choose to use. It returns a full message rather than just text, because the message may include tool-related output.

**Data flow**: It receives a system instruction, prior conversation messages, and tool descriptions. A concrete implementation will pass those to the model and return the next model message. The protocol only states this contract; it does not perform the call itself.

**Call relations**: Replay code can ask for a `ReplayLeg` when it needs a model turn with tools available. `ModelAccessLeg.turn` is the concrete adapter here that turns that request into the SDK’s `ModelRequest` format.


##### `ModelAccessLeg.complete`  (lines 28–37)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This sends a plain text completion request through the SDK’s model access layer. It is used when the extension needs the model to answer with text, not a tool-using message.

**Data flow**: It starts with a system instruction and conversation messages. It builds a `ModelRequest` using the wrapped model’s name, the shared 2048-token output limit, and reasoning set to off. It sends that request to `self.model.complete` and returns the text string that comes back.

**Call relations**: This is the real implementation behind the `ModelLeg.complete` promise. When proposal or grading code needs a straightforward model answer, it can call this method instead of constructing SDK requests itself. Inside, it hands the formatted request to the SDK model layer.

*Call graph*: 1 external calls (__init__).


##### `ModelAccessLeg.turn`  (lines 39–51)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This sends a tool-aware model turn through the SDK’s model access layer. It is used when replay logic needs the model to produce the next assistant message while knowing what tools are available.

**Data flow**: It receives a system instruction, conversation messages, and tool schemas, which are descriptions of tools the model may call. It packages them into a `ModelRequest` with the current model name, the shared token limit, and reasoning turned off. It sends the request to `self.model.turn` and returns the resulting `Message`.

**Call relations**: This is the real implementation behind the `ReplayLeg.turn` promise. Replay-oriented code calls it when it needs a full assistant message rather than just text. The method translates the extension’s narrow request into the SDK’s `ModelRequest` and passes it on to the underlying model access object.

*Call graph*: 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/proposer.py`

`domain_logic` · `self-improvement proposal generation`

This file is one step in a self-improvement loop. The system has noticed a class of tasks where an agent ran into friction. Instead of changing code directly, it asks another model to rewrite the agent’s system prompt so the agent has clearer instructions next time.

The main piece is PromptProposer. It receives the current prompt and a TaskClass, which contains the task name and examples of problems the agent had. If there are no examples, it does nothing, because there is no evidence to learn from. If examples exist, it builds a user-facing request that includes the task class, the current prompt, and a limited number of short examples. It sends that to a model together with a system instruction that says: improve the prompt, keep the agent’s broader purpose intact, and return only the revised prompt.

The result is cleaned up before use. This matters because models sometimes wrap answers in Markdown code fences, even when asked not to. If the model returns an empty answer, or simply repeats the existing prompt, the file treats that as no useful proposal and returns nothing.

The output, when successful, is a PromptCandidate: a small record tying the task class name to the proposed new prompt. Later parts of the system can evaluate or gate that candidate before adopting it.

#### Function details

##### `PromptProposer.propose`  (lines 33–43)

```
async def propose(self, current_prompt: str, task_class: TaskClass) -> PromptCandidate | None
```

**Purpose**: This is the main action in the file: it tries to create a revised system prompt for one task class. Someone would use it when the system has examples of an agent struggling and wants a model-written improvement to consider.

**Data flow**: It takes the current system prompt and a task class. First it checks whether the task class has mined examples; if not, it returns nothing. If examples exist, it builds a prompt for the model, sends that prompt as a user message along with fixed system instructions, cleans the model’s text reply, and compares it with the original prompt. The output is either a PromptCandidate containing the task name and revised prompt, or None if there was no useful change.

**Call relations**: This function drives the proposal step. It calls PromptProposer._prompt to prepare the text shown to the model, creates a Message object so the model receives that text in the expected chat format, calls the model through its ModelLeg, then calls _clean to remove unwanted wrapping. If the cleaned answer passes the basic checks, it creates and returns a PromptCandidate for later evaluation.

*Call graph*: calls 2 internal fn (_prompt, _clean); 2 external calls (__init__, __init__).


##### `PromptProposer._prompt`  (lines 45–56)

```
def _prompt(self, current_prompt: str, task_class: TaskClass) -> str
```

**Purpose**: This function writes the actual request that will be shown to the model. It packages the task class, the current prompt, and selected failure examples into a clear instruction asking for a full revised prompt.

**Data flow**: It takes the current prompt and a task class. It reads the task class name and its mined examples, trims each example’s request and problem text to a fixed length, and includes only up to the allowed maximum number of examples. It returns one formatted text block that says what task class is being improved, shows the current system prompt, lists examples, and asks for the full revised prompt.

**Call relations**: PromptProposer.propose calls this when it has decided there is enough evidence to ask the model for a rewrite. The text returned here becomes the content of the user message sent to the model.

*Call graph*: called by 1 (propose).


##### `_clean`  (lines 59–68)

```
def _clean(text: str) -> str
```

**Purpose**: This helper tidies the model’s answer so the rest of the system receives just the proposed prompt text. It mainly removes extra Markdown code fences that a model might add around its response.

**Data flow**: It takes the raw text returned by the model. It trims leading and trailing whitespace. If the result starts with a triple-backtick code fence, it removes the opening fence and, if present, the closing fence at the end. It then trims the result again and returns the cleaned prompt text.

**Call relations**: PromptProposer.propose calls this immediately after the model replies. The cleaned text is what gets checked for emptiness or no change before a PromptCandidate is created.

*Call graph*: called by 1 (propose).


### `extensions/self_improvement/ufo_ext_self_improvement/replay.py`

`domain_logic` · `self-improvement evaluation`

This file solves a practical testing problem: how can you tell whether a new prompt would have produced a better answer on an old task, without repeating actions that might touch the outside world? It does this by replaying the conversation like a stage rehearsal. The model is allowed to speak again under the new system prompt, but whenever it asks to use a tool, the code gives it the exact tool result that was saved from the original run instead of executing the tool again.

The replay starts by removing the original final answer, while keeping the user messages and previous tool results as context. It then builds a small fake tool catalog from the tools that appeared in the archive. During replay, if the model asks for the same tool with the same input, it receives the archived result. If it asks for a tool call that was not in the archive, the replay is marked as “diverged,” meaning it left the known path. The code then returns whatever answer text it had so far, because it cannot safely continue.

A round limit prevents endless back-and-forth. The final result records three things: the regenerated answer text, whether the replay diverged from the archive, and how many model turns it took.

#### Function details

##### `_canonical_input`  (lines 35–36)

```
def _canonical_input(value: object) -> str
```

**Purpose**: This helper turns a tool input into a stable text form so the same input can be recognized later. It sorts object keys and removes extra spacing, so equivalent inputs compare the same way.

**Data flow**: It receives any tool input value, such as a dictionary of arguments. It converts that value to a compact JSON string with keys in a predictable order. The output is that normalized string, used as part of a lookup key.

**Call relations**: When archived tool results are indexed, archived_tool_results uses this function to record each tool call by name and normalized input. Later, _feed_archived uses the same normalization on newly requested tool calls so it can find the matching archived result.

*Call graph*: called by 2 (_feed_archived, archived_tool_results); 1 external calls (dumps).


##### `replay_head`  (lines 39–52)

```
def replay_head(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This function prepares the archived conversation for replay by removing the old final answer. The new prompt should regenerate the answer itself, but it still needs the earlier user messages and tool history.

**Data flow**: It receives the full archived message list. Starting from the end, it drops trailing assistant messages unless one contains a tool request, because those trailing assistant messages are treated as final answer text. It returns the shortened message tuple that becomes the replay starting point.

**Call relations**: ReplayEvaluation.replay calls this near the beginning. The returned conversation is then passed into each model turn, giving the model the old context without letting it simply copy the old final response.

*Call graph*: called by 1 (replay).


##### `archived_tool_results`  (lines 55–77)

```
def archived_tool_results(messages: tuple[Message, ...]) -> dict[tuple[str, str], ToolResultBlock]
```

**Purpose**: This function builds a lookup table of saved tool answers from the archived conversation. It is what lets replay answer tool calls from history instead of touching live tools.

**Data flow**: It receives the archived messages. First it collects tool result blocks by their tool-use id. Then it finds each archived tool call, matches it to its saved result, and stores that result under a key made from the tool name and normalized input. It returns this dictionary of replayable tool answers.

**Call relations**: ReplayEvaluation.replay calls this before the model is run. Later, _feed_archived consults the returned table whenever the model asks for a tool result during replay.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay).


##### `replay_tools`  (lines 80–97)

```
def replay_tools(messages: tuple[Message, ...]) -> tuple[ToolSchema, ...]
```

**Purpose**: This function creates the limited tool list shown to the model during replay. It includes only tools that the archived run actually used, so the replay stays tied to the original path.

**Data flow**: It reads the archived messages and records each distinct tool name from tool-use blocks. For each name, it creates a permissive tool schema, meaning a simple description of a callable tool that accepts object-like inputs. It returns the tuple of these tool schemas.

**Call relations**: ReplayEvaluation.replay calls this during setup. The resulting tool list is passed to the model on each replay turn, giving the model enough information to reproduce archived tool calls without exposing the live system’s full tool registry.

*Call graph*: called by 1 (replay); 1 external calls (__init__).


##### `_feed_archived`  (lines 100–116)

```
def _feed_archived(tool_uses: tuple[ToolUseBlock, ...], results: Mapping[tuple[str, str], ToolResultBlock]) -> Message | None
```

**Purpose**: This function answers the model’s requested tool calls using saved results from the archive. If any requested call has no saved match, it signals that the replay has gone off the known path.

**Data flow**: It receives the tool calls from one model turn and the archived-result lookup table. For each call, it normalizes the input and searches for a saved result with the same tool name and input. If all are found, it creates a user message containing matching tool result blocks with the new call ids. If any result is missing, it returns None.

**Call relations**: ReplayEvaluation.replay calls this whenever the model asks to use tools. A returned message is appended to the replay conversation so the model can continue; a None return causes the replay to stop and be marked as diverged.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay); 2 external calls (__init__, __init__).


##### `ReplayEvaluation.replay`  (lines 129–150)

```
async def replay(self, archived: tuple[Message, ...], system_prompt: str) -> ReplayResult
```

**Purpose**: This is the main replay flow. It runs one archived task against one candidate system prompt, safely reusing archived tool results until the model gives a final answer, diverges, or reaches the round limit.

**Data flow**: It receives the archived conversation and the system prompt to test. It builds the archived tool-result lookup, creates the replay tool list, and strips away the original final answer. Then it repeatedly asks the model for the next assistant message. If the model gives final text without tool calls, it returns that as a successful replay. If the model asks for tools, it tries to feed back archived results; if that fails, it returns a diverged result with the best text seen so far. If too many rounds pass, it also returns a diverged result.

**Call relations**: This method ties together the whole file. It uses archived_tool_results, replay_tools, and replay_head for setup, calls the configured model for each replay leg, uses _feed_archived to answer tool calls from history, and finally produces a ReplayResult describing the outcome.

*Call graph*: calls 4 internal fn (_feed_archived, archived_tool_results, replay_head, replay_tools); 1 external calls (__init__).


### Scheduled task timers
Provides durable scheduled-task storage, due-task claiming, cron calculation, and workspace-facing tools for recurring reminders and reliable waits.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/runner.py`

`orchestration` · `recurring scheduled tick`

This runner is the part of the scheduled-tasks extension that wakes up at regular intervals and asks, “What should fire now?” It is not triggered by the schedule records themselves; it is more like a caretaker making rounds on a clock. Without it, due tasks would sit in storage and never be delivered back into their conversations.

The runner first checks that a scheduler has been connected to the extension context. It then records the current time and asks the schedule store for due tasks, claiming each one with a lease. A lease is a temporary reservation, like putting a sticky note on a library book, so another overlapping runner tick does not process the same task at the same time.

For each claimed task, it checks whether the task has expired before firing. If it has, the task is retired and nothing is invoked. If it is still valid, the runner works out the next fire time for repeating schedules. When the next fire would be at or after the task’s expiry, it adds a special final-fire instruction telling the conversation to finish the task and ask the user whether to continue, change, or stop the cadence.

If invocation succeeds, repeating tasks are rescheduled only after the turn is accepted. If invocation fails, the failure is remembered and the leased occurrence is left for retry. At the end of the tick, any failures are raised together by task name.

#### Function details

##### `ScheduledTaskRunner.run`  (lines 36–47)

```
async def run(self) -> None
```

**Purpose**: Runs one scheduled-task polling tick. It claims all tasks that are due now, asks the runner to fire each one, and reports any task fires that failed.

**Data flow**: It reads the extension context to find the scheduler. It takes the current time, asks the scheduler for due tasks, and passes each claimed task into ScheduledTaskRunner._fire. If every task succeeds or is skipped because it no longer applies, it returns with no value; if any task fails to fire, it raises an error listing the failed task names.

**Call relations**: This is the outer loop for one clock tick. It calls ScheduledTaskRunner._fire for the detailed work on each claimed task, using the current time from the system clock to decide what is due and to help evaluate expiry.

*Call graph*: calls 1 internal fn (_fire); 1 external calls (now).


##### `ScheduledTaskRunner._fire`  (lines 49–83)

```
async def _fire(self, scheduler: ScheduleStore, task: ScheduledTask, tick_at: datetime, expiry_checked_at: datetime) -> str | None
```

**Purpose**: Processes one claimed scheduled task. It retires expired tasks, invokes active ones back into their conversation, and advances repeating tasks to their next fire time when the invocation is accepted.

**Data flow**: It receives the scheduler, the claimed task, the tick time, and the time used for the expiry check. First it asks the scheduler whether the task should be retired because it expired. If not, it calculates the next fire time for repeating schedules, prepares a final-fire instruction when this is the last allowed occurrence, and asks the scheduler to invoke the task. If invocation fails, it returns a short failure label; if invocation succeeds and the task repeats, it asks the scheduler to reschedule the task for the following fire time. The result is either no failure or a task-name failure string.

**Call relations**: ScheduledTaskRunner.run calls this once for each due claimed task. Inside, it relies on the schedule store to retire expired work, invoke the task, and reschedule repeating work, and it uses next_fire to calculate the next cron-style occurrence when the task is not one-time.

*Call graph*: calls 3 internal fn (invoke, reschedule, retire_if_expired); called by 1 (run); 1 external calls (next_fire).


### `core/src/ufo/scheduling.py`

`domain_logic` · `request handling and background scheduled-task polling`

A scheduled task is like an appointment card kept in the database: it says which workspace and conversation it belongs to, which agent should wake up, what prompt to send, and when it should run next. Without this file, scheduled work would either disappear when the process restarts or be fired twice by competing background workers.

The file defines a plain in-memory shape, `ScheduledTask`, for one database row, plus `ScheduleStore`, the main doorway for reading and changing those rows. Member-facing actions use the current workspace and current object agent, so a task cannot accidentally be attached to the wrong agent or conversation. Recurring tasks can be created, edited, cancelled, listed, and inspected.

It also supports one-time workflow pauses. These are special tasks named with an internal prefix and scheduled as `@once`; normal create, update, and cancel paths reject them so they are not mistaken for user-defined recurring jobs.

For background scheduling, the file carefully leases due tasks. A lease is a short temporary claim, like putting a sticky note on a job saying “worker A is doing this until 10:05.” Expired tasks are removed, due tasks are claimed in bounded batches, and claims are checked when retiring or rescheduling. This keeps overlapping pollers from doing the same work twice.

#### Function details

##### `ScheduleInvoker.invoke_scheduled`  (lines 56–58)

```
async def invoke_scheduled(self, task: ScheduledTask, runtime_instruction: str | None=None) -> UUID | None
```

**Purpose**: This is an interface method for something that knows how to actually run a scheduled task. The store owns the saved schedule, but an invoker owns the act of re-entering the agent conversation.

**Data flow**: It receives a `ScheduledTask` and an optional runtime instruction. An implementation uses those details to start or resume work, then returns the new turn identifier if one was created, or nothing if no turn was admitted.

**Call relations**: The method is not implemented here; it is a promise that another part of the system fulfills. `ScheduleStore.invoke` calls it when the scheduled-task runner is ready to fire a claimed task.


##### `_utc`  (lines 94–97)

```
def _utc(value: datetime | None) -> datetime | None
```

**Purpose**: This small helper makes database timestamps easier to compare by ensuring a timestamp has UTC timezone information when it was stored without one. It leaves missing values and already timezone-aware values alone.

**Data flow**: It takes either a datetime or `None`. If the datetime has no timezone attached, it returns the same clock time marked as UTC; otherwise it returns the original value.

**Call relations**: Rows from the database pass through `_task` and `ScheduleStore.inspect`, and both use `_utc` before exposing expiration times to the rest of the scheduling code.

*Call graph*: called by 2 (inspect, _task); 1 external calls (replace).


##### `_claim_available`  (lines 100–104)

```
def _claim_available(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition for “this scheduled task is free to take.” A task is available if nobody has claimed it, or if its old claim has timed out.

**Data flow**: It receives the current time. It produces a SQL condition that can be used in a database query to find rows whose `claimed_by` field is empty or whose claim expiration is in the past.

**Call relations**: The candidate finder and the due-task claimer both use this same rule, so the system agrees on which tasks are really available for workers.

*Call graph*: called by 2 (claim_due, candidates); 1 external calls (or_).


##### `_expired`  (lines 107–111)

```
def _expired(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition for “this scheduled task has passed its expiration time.” Expired tasks should not be fired again.

**Data flow**: It receives the current time. It produces a SQL condition that matches tasks with an `expires_at` value that is not empty and is at or before that time.

**Call relations**: The workspace candidate finder uses it to notice workspaces that need cleanup, and `ScheduleStore.claim_due` uses it to delete expired rows before claiming due work.

*Call graph*: called by 2 (claim_due, candidates); 1 external calls (and_).


##### `_task`  (lines 114–132)

```
def _task(row: sa.RowMapping) -> ScheduledTask
```

**Purpose**: This turns a raw database row into a `ScheduledTask` object that the rest of the Python code can use safely and consistently.

**Data flow**: It receives a database row mapping with scheduled-task columns. It copies the row fields into a `ScheduledTask`, normalizes the expiration timestamp through `_utc`, and returns the new in-memory object.

**Call relations**: Create, update, list, and claim operations all receive rows from the database and pass them through `_task` before returning them to callers.

*Call graph*: calls 1 internal fn (_utc); called by 4 (claim_due, create, list, update); 1 external calls (__init__).


##### `due_task_workspaces`  (lines 135–162)

```
def due_task_workspaces() -> WorkspaceCandidates
```

**Purpose**: This creates the background runner’s workspace finder. It answers the question, “Which workspaces might have scheduled tasks ready to run or clean up?”

**Data flow**: It creates and returns an async candidate function. That function later reads the database and returns workspace IDs, not tasks themselves.

**Call relations**: The scheduled-task runner can use this as its first filter. It finds promising workspaces globally, then the runner binds to each workspace before touching the actual task rows.


##### `due_task_workspaces.candidates`  (lines 142–160)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: This is the actual database lookup behind `due_task_workspaces`. It finds workspaces that contain at least one claim-available due task or expired task.

**Data flow**: It reads the current UTC time, builds conditions for available claims and expired tasks, then runs an owner-level database query that returns distinct workspace IDs. It outputs those IDs as a tuple.

**Call relations**: It uses `_claim_available` and `_expired` so it matches the same rules later used by `ScheduleStore.claim_due`. It only returns workspace IDs; later code must enter the workspace context before claiming tasks.

*Call graph*: calls 2 internal fn (_claim_available, _expired); 4 external calls (now, or_, select, owner_tx).


##### `ScheduleStore.workspace_id`  (lines 176–177)

```
def workspace_id(self) -> UUID
```

**Purpose**: This property returns the workspace that the current operation is scoped to. It prevents schedule operations from accidentally crossing workspace boundaries.

**Data flow**: It reads the current workspace context through `ws_current()` and returns its workspace ID.

**Call relations**: Most `ScheduleStore` methods use this property when building database queries, so every create, update, delete, claim, and read is tied to the active workspace.

*Call graph*: 1 external calls (ws_current).


##### `ScheduleStore.invoke`  (lines 179–184)

```
async def invoke(self, task: ScheduledTask, runtime_instruction: str | None=None) -> UUID | None
```

**Purpose**: This asks the configured invoker to actually run a scheduled task. It exists so the storage layer can delegate execution without knowing the details of agent runtime.

**Data flow**: It receives a `ScheduledTask` and optional runtime instruction. If no invoker was provided when the store was built, it raises an error; otherwise it passes the task to the invoker and returns the resulting turn ID or `None`.

**Call relations**: The scheduled-task runner calls this while firing a claimed task. This method then hands off to `ScheduleInvoker.invoke_scheduled`, which is supplied by another part of the system.

*Call graph*: called by 1 (_fire).


##### `ScheduleStore.create`  (lines 186–260)

```
async def create(self, conversation_id: UUID, name: str, schedule: str, prompt: str, description: str, next_run_at: datetime, created_by_member_id: UUID | None=None, expires_at: datetime | None=None)
```

**Purpose**: This creates a new recurring scheduled task for the current agent and conversation. It checks that the task is valid and unique before saving it.

**Data flow**: It receives the conversation ID, task name, schedule text, prompt, description, next run time, optional creator, and optional expiration. It rejects one-time pause names and schedules, verifies that the conversation belongs to the current agent, inserts a row, and returns it as a `ScheduledTask`; if a task with the same workspace, agent, and name already exists, it raises an error.

**Call relations**: User-facing scheduling code calls this when defining a recurring task. It uses `object_agent_id` to bind the task to the current agent, `workspace_tx` for the database transaction, and `_task` to convert the saved row into the return value.

*Call graph*: calls 1 internal fn (_task); 4 external calls (select, workspace_tx, object_agent_id, uuid4).


##### `ScheduleStore.update`  (lines 262–319)

```
async def update(self, expected: ScheduledTask, schedule: str, prompt: str, description: str, next_run_at: datetime, expires_at: datetime | None=None) -> ScheduledTask
```

**Purpose**: This edits an existing recurring scheduled task without changing its identity: same task ID, same name, same conversation, same agent, and same creator. That protects against overwriting a task that changed underneath the editor.

**Data flow**: It receives the `ScheduledTask` the caller believes is current, plus replacement schedule, prompt, description, next run time, and optional expiration. It rejects one-time pauses, confirms the current agent still matches, updates the exact matching row, clears old run and claim markers, and returns the updated task. If no exact row matches, it raises an error.

**Call relations**: Editing flows call this after they have an existing task object. It uses the database as the source of truth and `_task` to return the fresh row.

*Call graph*: calls 1 internal fn (_task); 3 external calls (update, workspace_tx, object_agent_id).


##### `ScheduleStore.pause`  (lines 321–338)

```
async def pause(self, conversation_id: UUID, prompt: str, description: str, next_run_at: datetime, origin_seq: int, created_by_member_id: UUID | None=None) -> ScheduledTask | None
```

**Purpose**: This arms a one-time pause for a conversation. A pause is a special scheduled wake-up used by workflow logic, not a normal recurring user task.

**Data flow**: It receives the conversation, prompt, description, desired wake time, originating conversation sequence number, and optional creator. It simply forwards those details to `_upsert_pause` and returns either the saved pause task or `None` if a member reply means the pause should not be armed.

**Call relations**: Higher-level workflow code calls this public method. The real checks and database upsert are done by `ScheduleStore._upsert_pause`.

*Call graph*: calls 1 internal fn (_upsert_pause).


##### `ScheduleStore._upsert_pause`  (lines 340–482)

```
async def _upsert_pause(self, conversation_id: UUID, prompt: str, description: str, next_run_at: datetime, origin_seq: int, created_by_member_id: UUID | None) -> ScheduledTask | None
```

**Purpose**: This creates or replaces the special one-time pause task for a conversation. It also avoids scheduling a timer when a human member has already replied in a way that should resume the conversation instead.

**Data flow**: It receives pause details and reads the current agent and workspace. It verifies the conversation belongs to that agent, checks for unconsumed member messages, looks for newer member turns after the origin sequence, and may return `None` if the pause should not happen. If a queued member turn should resume the workflow, it records that turn and makes the task due immediately. It then inserts or updates the special `@pause:<conversation_id>` task and returns a `ScheduledTask` object.

**Call relations**: `ScheduleStore.pause` calls this helper. It uses database reads and locks to make member replies and timer recovery converge on one durable next step instead of racing each other.

*Call graph*: called by 1 (pause); 7 external calls (__init__, now, exists, select, workspace_tx, object_agent_id, uuid4).


##### `ScheduleStore.cancel`  (lines 484–507)

```
async def cancel(self, expected: ScheduledTask) -> None
```

**Purpose**: This deletes a recurring scheduled task, but only if it still matches the exact task the caller expected. That prevents accidentally cancelling a different task after a concurrent edit.

**Data flow**: It receives the expected `ScheduledTask`. It rejects one-time pauses, confirms the current agent matches, deletes the row that matches the task’s ID, agent, conversation, name, workspace, and creator, and returns nothing. If no row was deleted, it raises an error.

**Call relations**: User-facing cancellation flows call this for recurring tasks. It uses `object_agent_id` and `workspace_tx` to keep the delete inside the current agent and workspace boundary.

*Call graph*: 3 external calls (delete, workspace_tx, object_agent_id).


##### `ScheduleStore.list`  (lines 509–527)

```
async def list(self) -> tuple[ScheduledTask, ...]
```

**Purpose**: This returns all recurring scheduled tasks visible to the current agent in the current workspace. It deliberately excludes one-time workflow pauses.

**Data flow**: It reads the current agent and workspace, queries scheduled-task rows whose schedule is not `@once`, orders them by name, converts each row through `_task`, and returns them as a tuple.

**Call relations**: Status pages or object reads can call this to show a user’s recurring schedules. It relies on `_task` so callers get normal `ScheduledTask` objects instead of raw database rows.

*Call graph*: calls 1 internal fn (_task); 3 external calls (select, workspace_tx, object_agent_id).


##### `ScheduleStore.claim_due`  (lines 529–583)

```
async def claim_due(self, now: datetime, lease_seconds: int, limit: int=CLAIM_BATCH_MAX_TASKS) -> tuple[ScheduledTask, ...]
```

**Purpose**: This is the worker-safe way to take scheduled tasks that are ready to run. It cleans up expired tasks, then places a short lease on a limited batch of due tasks so other workers do not run the same ones.

**Data flow**: It receives the current time, a lease length in seconds, and an optional batch limit. It creates a unique claim ID, deletes expired tasks that are free to touch, selects the oldest due available tasks up to the limit, marks them with the claim and claim expiration, and returns them as `ScheduledTask` objects.

**Call relations**: The scheduled-task runner calls this after it has selected a workspace. It uses `_claim_available` and `_expired` to match the candidate rules, then `_task` to return claimed tasks ready for firing.

*Call graph*: calls 3 internal fn (_claim_available, _expired, _task); 7 external calls (timedelta, delete, not_, select, update, workspace_tx, uuid4).


##### `ScheduleStore.retire_if_expired`  (lines 585–599)

```
async def retire_if_expired(self, task: ScheduledTask, now: datetime) -> bool
```

**Purpose**: This deletes a claimed task if it expired before the worker could invoke it. It is a final safety check before firing.

**Data flow**: It receives a claimed `ScheduledTask` and the current time. If the task is not claimed, it raises an error. If the task has no expiration or has not yet expired, it returns `False`. If it is expired, it deletes the exact row with the matching claim and returns `True`.

**Call relations**: The scheduled-task runner calls this during `_fire` before invoking the task. The claim check means a worker only retires the task it actually leased.

*Call graph*: called by 1 (_fire); 2 external calls (delete, workspace_tx).


##### `ScheduleStore.reschedule`  (lines 601–634)

```
async def reschedule(self, task: ScheduledTask, next_run_at: datetime, last_run_at: datetime, last_turn_id: UUID | None=None) -> bool
```

**Purpose**: This advances a recurring task after it has fired. It records when it ran, optionally records the turn it created, sets the next run time, and releases the worker’s claim.

**Data flow**: It receives a claimed recurring task, the next run time, the last run time, and optionally the turn ID created by the fire. It rejects unclaimed tasks and one-time pauses, updates the exact claimed database row, clears claim fields, and returns `True` if the row was updated or `False` if the claim no longer matched.

**Call relations**: The scheduled-task runner calls this after a successful fire. Later, `ScheduleStore.inspect` can use the recorded last turn to show the latest result.

*Call graph*: called by 1 (_fire); 2 external calls (update, workspace_tx).


##### `ScheduleStore.inspect`  (lines 636–675)

```
async def inspect(self, expected: ScheduledTask) -> TaskInspection | None
```

**Purpose**: This reads the live status of one recurring task, including its timing and the latest turn it produced. It is used for status rendering rather than for changing the schedule.

**Data flow**: It receives the expected task. It queries the matching task row for the current workspace and agent, joins to the last turn if one was recorded, and returns a `TaskInspection` with next run, last run, expiration, last turn status, and final response text. If the task no longer exists or is a one-time pause, it returns `None`.

**Call relations**: Status views call this when they need more than the task definition. It uses `_utc` for the expiration timestamp and reads the turn table so users can see the latest scheduled run outcome.

*Call graph*: calls 1 internal fn (_utc); 4 external calls (__init__, select, workspace_tx, object_agent_id).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/cron.py`

`domain_logic` · `schedule creation and scheduled-task polling`

Scheduled tasks need a way to say “run every day at 9” or “run every five minutes.” This file is the small place where that timing language lives. It uses cron expressions, which are compact five-part strings that describe minute, hour, day of month, month, and day of week.

The rest of the scheduled-task system stores a simple date and time called `next_run_at`. It does not need to understand cron itself. That is important because it keeps the storage layer simple: it only sorts tasks by their next due time, like a calendar app sorting reminders. This file translates the human schedule rule into the next concrete reminder time.

There are two key behaviors. First, `validate_cron` rejects schedules that are not five-field cron expressions or are not accepted by the cron parser. Second, `next_fire` asks the cron library for the next scheduled time strictly after a given moment. That “strictly after” detail matters: if the runner was asleep or delayed, it does not create a separate run for every missed tick. Instead, it moves forward to one next catch-up time, avoiding a sudden burst of overdue jobs.

#### Function details

##### `validate_cron`  (lines 14–19)

```
def validate_cron(schedule: str) -> str
```

**Purpose**: Checks whether a schedule string is a valid five-field cron expression. It is used to catch bad schedules early, before they are stored or used to calculate future task runs.

**Data flow**: A schedule string goes in. The function first splits it into space-separated parts and makes sure there are exactly five, matching the expected cron format. It then asks the cron parsing library whether the expression is valid. If anything is wrong, it raises a clear error; if everything is acceptable, it returns the original schedule unchanged.

**Call relations**: When some higher-level scheduling code wants to accept or save a cron schedule, this function is the gatekeeper. Inside, it hands the final validity check to `croniter.croniter.is_valid`, which knows the detailed cron rules.

*Call graph*: 1 external calls (is_valid).


##### `next_fire`  (lines 22–23)

```
def next_fire(schedule: str, after: datetime) -> datetime
```

**Purpose**: Calculates the next time a cron schedule should run after a given moment. This turns a repeating rule into one exact date and time the rest of the system can store and compare.

**Data flow**: A cron schedule and a starting datetime go in. The function gives both to the cron library, which walks forward through the schedule. The result is a datetime for the next matching run time after the supplied moment.

**Call relations**: The scheduled-task runner or update logic calls this when it needs to set the next `next_run_at` value. This function delegates the calendar math to `croniter.croniter`, then returns the concrete next fire time to the caller.

*Call graph*: 1 external calls (croniter).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/tools.py`

`domain_logic` · `request handling and scheduled workflow pauses`

This file is the bridge between the agent-facing tools and the scheduling storage behind them. A scheduled task is treated like a workspace object: it has a name, a schedule, a prompt to run later, and ownership rules. The file defines the shape of that object, checks that expiry times are valid UTC times, and supplies the logic for listing, reading, creating, changing, and cancelling tasks.

The most important rule is privacy. A task belongs to the member who created it. That creator can see and edit the task content. An admin can see management details and change timing, but cannot rewrite another member’s prompt or description. This matters because scheduled task runs act on behalf of the original creator, so changing the prompt would be like putting words into someone else’s mouth.

The file also defines `pause_and_wait`, a separate tool for durable pauses. “Durable” means the wait is saved in the scheduler, so it can resume later even after time passes. It is like leaving a sticky note for the future: either a member message wakes the workflow, or a timer does. One-shot pause rows are internal scheduler records, not public scheduled-task objects.

#### Function details

##### `ScheduledTaskSpec.validate_utc_expiry`  (lines 75–78)

```
def validate_utc_expiry(cls, value: datetime | None) -> datetime | None
```

**Purpose**: This checks that a task expiry time is written as a real UTC timestamp. UTC is the shared world clock used here so schedules do not shift depending on a user’s local time zone.

**Data flow**: It receives the optional `expires_at` value from a task specification. If there is no expiry, it lets that through. If there is an expiry, it checks that the timestamp has UTC time-zone information; a non-UTC or timezone-less value is rejected with a clear error. The output is the same valid value, ready to store.

**Call relations**: This validation runs as part of building or checking a `ScheduledTaskSpec`. It uses the timestamp’s own offset information and compares it to zero offset, which means UTC.

*Call graph*: 2 external calls (utcoffset, timedelta).


##### `_require_scheduler`  (lines 99–102)

```
def _require_scheduler(ctx: ToolContext) -> ScheduleStore
```

**Purpose**: This is a safety check that retrieves the scheduler store from the current tool context. It makes sure scheduled-task code is only used when the scheduled-tasks extension has actually been installed and wired in.

**Data flow**: It takes the current `ToolContext`, looks inside its extension area, and expects to find a scheduler store there. If the store exists, it returns it. If not, it stops immediately with a runtime error instead of letting later code fail in a confusing way.

**Call relations**: The task object methods and `pause_and_wait` call this before reading or writing schedules. It is the common doorway to `ScheduleStore`, so create, update, delete, list, inspect, find, and pause operations all start by passing through this check.

*Call graph*: called by 6 (_apply_owned, _delete_owned, _find, _owned_rows, _status, pause_and_wait).


##### `_summary`  (lines 105–106)

```
def _summary(task: ScheduledTask) -> str
```

**Purpose**: This creates the short one-line summary shown when scheduled tasks are listed. It gives a quick human-readable glimpse of the schedule and what the task is for.

**Data flow**: It receives a stored scheduled task. It combines the cron schedule with either the task description or, if there is no description, the prompt itself. Then it cuts the text down to a fixed maximum length so listings stay compact.

**Call relations**: `ScheduledTaskObjects._owned_rows` calls this when the current viewer is allowed to see the task content. If the content is private, the listing uses a safer generic summary instead.

*Call graph*: called by 1 (_owned_rows).


##### `ScheduledTaskObjects._admin_can_apply`  (lines 126–127)

```
def _admin_can_apply(self, _old: ScheduledTaskSpec, spec: ScheduledTaskSpec) -> bool
```

**Purpose**: This decides whether an admin is allowed to apply a proposed update to someone else’s scheduled task. Admins may adjust timing details, but they may not change the prompt or description that will run as the original creator.

**Data flow**: It receives the old task spec and the proposed new spec. It looks only at which fields were included in the update. If the update includes `prompt` or `description`, it returns false for admin-only editing; otherwise it returns true.

**Call relations**: This method supports the broader object-permission flow supplied by the member-owned object base class. It encodes this file’s privacy rule: scheduling metadata is administratively adjustable, but task content stays under the creator’s control.


##### `ScheduledTaskObjects._owned_rows`  (lines 129–145)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This builds the list entries for scheduled tasks that exist in the scheduler. Each entry includes the task name, a safe summary, and generated ownership information used by the object system.

**Data flow**: It takes the current context, gets the scheduler store, and asks it for all visible scheduled task rows. For each task, it creates an owned-row record. If the current speaker may see the content, the summary includes the description or prompt; otherwise it only says it is a private member task. The result is a tuple of listing rows.

**Call relations**: The object system uses this method when scheduled tasks are listed. It calls `_require_scheduler` to reach storage, `_content_visible` to protect private content, and `_summary` to make readable list text.

*Call graph*: calls 3 internal fn (_content_visible, _require_scheduler, _summary); 2 external calls (__init__, __init__).


##### `ScheduledTaskObjects._detail`  (lines 147–169)

```
async def _detail(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> ObjectDetail[ScheduledTaskSpec] | None
```

**Purpose**: This returns the full object-style details for one scheduled task, when the requested task still matches the expected stored generation. It lets the object system show the task spec, timestamps, and where the task reports.

**Data flow**: It receives the current context, a task name, and an owner token that includes the expected stored generation. It looks up the task by name. If the task is missing or has changed generation, it returns nothing. Otherwise it packages the schedule, prompt, description, expiry, creation and update times, and a link back to the conversation where task runs report.

**Call relations**: This is used by the object-read path for scheduled tasks. It calls `_find` to locate the task and `_content_visible` to decide whether the full spec should be visible to the requester.

*Call graph*: calls 2 internal fn (_content_visible, _find); 4 external calls (__init__, __init__, __init__, __init__).


##### `ScheduledTaskObjects._status`  (lines 171–201)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This produces the live status view for a scheduled task: when it will run next, when it last ran, whether it expires, and a small safe glimpse of the last run if allowed.

**Data flow**: It receives the current context, task name, and expected owner generation. It finds the task, verifies it is still the same stored task, and asks the scheduler to inspect it. If inspection succeeds, it returns a dictionary containing the next run time, last run time, expiry, and last-run information. If the viewer can see the task content, the last response may be included as a shortened excerpt.

**Call relations**: The object system calls this when it needs status information beyond the saved spec. It uses `_find` for identity checking, `_require_scheduler` to ask storage for inspection data, and `_content_visible` to avoid leaking private output.

*Call graph*: calls 3 internal fn (_content_visible, _find, _require_scheduler).


##### `ScheduledTaskObjects._apply_owned`  (lines 203–252)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ScheduledTaskSpec, old: ScheduledTaskSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: This is the main create-or-update path for scheduled task objects. It validates the schedule, enforces ownership rules, and writes the new or changed task into the scheduler.

**Data flow**: It receives the current context, the object name, the requested spec, the previous spec if known, and the owner record if this is an update. First it validates any cron schedule. Then it requires an acting member, because scheduled tasks run as a real member. For a new task, it requires both schedule and prompt, calculates the first run time, and creates the scheduler record tied to the current conversation. For an existing task, it checks that the stored task still matches, checks whether the speaker is the creator or an admin, preserves omitted fields, recalculates the next run time, and updates the scheduler record.

**Call relations**: This method is called by the object-apply flow when a scheduled-task manifest is created or re-applied. It relies on `_find` for change detection, `_require_scheduler` for storage, `validate_cron` to reject bad schedules, `next_fire` to compute the next run, and `speaker_is_admin` when permission depends on admin status.

*Call graph*: calls 3 internal fn (speaker_is_admin, _find, _require_scheduler); 4 external calls (__init__, now, next_fire, validate_cron).


##### `ScheduledTaskObjects._delete_owned`  (lines 254–258)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: This cancels a scheduled task after confirming that the task being deleted is still the exact stored generation the caller meant to delete. That prevents accidentally cancelling a task that changed while someone was editing.

**Data flow**: It receives the current context, task name, and expected owner generation. It finds the current stored task. If the task is missing or has a different generation, it raises an error. If it matches, it asks the scheduler store to cancel it.

**Call relations**: The object-delete flow uses this method for scheduled-task cancellation. It calls `_find` to confirm identity and `_require_scheduler` to reach the scheduler’s cancel operation.

*Call graph*: calls 2 internal fn (_find, _require_scheduler).


##### `ScheduledTaskObjects._find`  (lines 260–263)

```
async def _find(self, ctx: ToolContext, name: str) -> ScheduledTask | None
```

**Purpose**: This searches the scheduler’s task list for a scheduled task with a specific name. It is a small helper that keeps the lookup behavior consistent across read, status, update, and delete operations.

**Data flow**: It takes the current context and a task name. It retrieves the scheduler, asks for the stored task list, and returns the first task whose name matches. If none match, it returns nothing.

**Call relations**: `_apply_owned`, `_delete_owned`, `_detail`, and `_status` all call this before acting on one named task. It delegates the actual storage access to `_require_scheduler`.

*Call graph*: calls 1 internal fn (_require_scheduler); called by 4 (_apply_owned, _delete_owned, _detail, _status).


##### `ScheduledTaskObjects._content_visible`  (lines 265–268)

```
def _content_visible(self, ctx: ToolContext, task: ScheduledTask) -> bool
```

**Purpose**: This answers the privacy question: may the current speaker see the task’s prompt, description, and response excerpts? It keeps private task content from being shown to other members.

**Data flow**: It receives the current context and a stored task. If the task has no recorded creator, or if the creator matches the current acting member, it returns true. Otherwise it returns false.

**Call relations**: `_owned_rows`, `_detail`, and `_status` call this before including task content in listings, detailed specs, or last-run responses. It is the simple privacy gate used throughout the file.

*Call graph*: called by 3 (_detail, _owned_rows, _status).


##### `pause_and_wait`  (lines 303–339)

```
async def pause_and_wait(ctx: ToolContext, args: PauseAndWaitInput) -> ToolResult
```

**Purpose**: This tool pauses an ongoing workflow until either a new member message arrives or a durable timer fires. It is meant for real-world waits such as approval, verification email, or cooldown periods.

**Data flow**: It receives the tool context and pause instructions: what to say now, how many minutes to wait, what to do after resuming, why it is waiting, optional saved metadata, and a user-facing description. It computes a resume time, writes a pause record into the scheduler, and then returns instructions for the agent’s immediate reply. If a newer member message has already arrived, it tells the agent that no timer was armed and that the message will resume the workflow. Otherwise it tells the agent that the workflow is awaiting the timer.

**Call relations**: This is the handler behind the `pause_and_wait` tool definition. It calls `_require_scheduler` to create the durable pause, uses JSON text to pass resume instructions forward, and returns a `ToolResult` containing a directive the agent should follow before ending its turn.

*Call graph*: calls 1 internal fn (_require_scheduler); 5 external calls (__init__, __init__, now, timedelta, dumps).


### Workspace background jobs
Discovers workspaces with pending background work, runs jobs safely inside workspace context, and handles deferred Slack Connect invitation fulfillment.

### `control/src/ufo_control/gateway_slack_connect.py`

`orchestration` · `background polling during gateway runtime`

This file solves a reliability problem: Slack invitations are useful, but Slack can be slow, rate-limited, or temporarily unavailable. Instead of doing Slack work during signup, the system records the completed signup in the database and lets this background worker pick it up later. Think of it like a mailroom: signup drops a durable request in a tray, and this worker keeps checking the tray until the invitation has been sent or clearly needs human help.

The workflow first creates delivery rows for eligible signup claims. Only claims that used an invite code and created a new workspace qualify, and only the earliest claim for a workspace is used. It then leases one due row, so two gateway replicas do not work on the same customer at the same time. While leased, it verifies the Slack bot token belongs to the expected operator workspace, creates or finds the deterministic channel name, and sends a Slack Connect invite to the signup email.

The file is careful about duplicate invitations. It records that an invite is being attempted before calling Slack, and if a response is lost it checks Slack’s own invite and sharing state before trying again. Temporary failures are retried with backoff. Permanent failures are saved as failed rows for operator review. Tokens are redacted from stored errors and logs.

#### Function details

##### `SlackTransientError.__init__`  (lines 167–169)

```
def __init__(self, message: str, retry_after: float | None=None) -> None
```

**Purpose**: This creates an error object for Slack problems that may clear up on their own, such as a network timeout or Slack rate limit. It can also carry a suggested wait time before trying again.

**Data flow**: It receives an error message and, optionally, a retry-after delay. It stores the message like a normal runtime error and saves the delay on the error object, so later code can decide when to retry.

**Call relations**: SlackConnectClient._call raises this when an HTTP call to Slack fails in a way that looks temporary. Later, the inviter catches this kind of error and reschedules the delivery instead of marking it permanently failed.

*Call graph*: called by 1 (_call).


##### `rearm_failed_delivery`  (lines 188–203)

```
async def rearm_failed_delivery(pool: asyncpg.Pool, onboard_claim_id: UUID) -> datetime | None
```

**Purpose**: This is the operator recovery hook for retrying one failed Slack Connect delivery after a human has fixed the cause. It only resets failed rows; it does not touch rows that already succeeded.

**Data flow**: It receives a database pool and a signup claim ID. It looks for a matching delivery row in the failed state, resets it back to pending, clears old worker, retry, attempt, and error fields, and returns the time it had last been updated. If there is no failed row to reset, it returns nothing.

**Call relations**: This function is separate from the automatic poller. An operator command can call it when a row needs another try; the normal SlackConnectInviter polling loop will then pick up the re-armed row later.

*Call graph*: 1 external calls (fetchval).


##### `SlackConnectClient.team_id`  (lines 215–216)

```
async def team_id(self) -> str
```

**Purpose**: This asks Slack which workspace the configured bot token belongs to. It is used as a safety check before creating or changing channels.

**Data flow**: It sends an auth.test request to Slack through _call, then uses _text to pull the team_id value out of Slack’s response. The result is the Slack workspace ID as text.

**Call relations**: SlackConnectInviter._verify_team calls this before any delivery mutates Slack. It relies on _call for the HTTP request and _text for safe response reading.

*Call graph*: calls 2 internal fn (_call, _text).


##### `SlackConnectClient.create_channel`  (lines 218–220)

```
async def create_channel(self, name: str) -> str
```

**Purpose**: This creates a public Slack channel with the given deterministic name. The channel becomes the place where the customer’s Slack Connect invitation will point.

**Data flow**: It receives a channel name, sends it to Slack’s conversations.create API, and reads the new channel ID from the response. It returns that channel ID.

**Call relations**: SlackConnectInviter._open_channel calls this when the database row does not yet have a channel ID. If Slack says the name is already taken, _open_channel recovers by looking up the existing channel instead.

*Call graph*: calls 2 internal fn (_call, _text).


##### `SlackConnectClient.channel_id_by_name`  (lines 222–243)

```
async def channel_id_by_name(self, name: str) -> str
```

**Purpose**: This finds the Slack channel ID for an exact channel name, including archived channels. It is the recovery path when Slack says a channel name already exists.

**Data flow**: It receives a channel name, pages through Slack’s public channel list, and compares each channel’s name exactly. If it finds a match, it returns that channel’s ID. If it cannot find the channel within the bounded search, it raises a permanent error because Slack’s state is inconsistent from this worker’s point of view.

**Call relations**: SlackConnectInviter._open_channel uses this after create_channel hits a name-taken error. It calls _call for each Slack page and _text to extract the matching channel ID safely.

*Call graph*: calls 2 internal fn (_call, _text); 1 external calls (__init__).


##### `SlackConnectClient.outgoing_invite_id`  (lines 245–276)

```
async def outgoing_invite_id(self, channel_id: str) -> str | None
```

**Purpose**: This checks whether a live Slack Connect invitation already exists for a channel. It helps avoid sending a duplicate invite when a previous attempt may have reached Slack but the response was lost.

**Data flow**: It receives a Slack channel ID, pages through Slack’s outgoing Connect invitations, and looks for entries for that channel. If it finds a live invitation, it returns the invitation ID. If it finds only dead invitations, it returns nothing. If Slack reports an unknown status or there are too many pages to inspect safely, it raises a permanent error for human review.

**Call relations**: SlackConnectInviter._invite calls this only when the database shows an invite was attempted before. It uses _call to read Slack’s invite list and _text to extract the live invitation ID.

*Call graph*: calls 2 internal fn (_call, _text); 1 external calls (__init__).


##### `SlackConnectClient.is_externally_shared`  (lines 278–281)

```
async def is_externally_shared(self, channel_id: str) -> bool
```

**Purpose**: This checks whether a Slack channel is already shared or pending sharing with an external workspace. It is another safety check before deciding whether a new invitation is needed.

**Data flow**: It receives a channel ID, asks Slack for channel details, and reads Slack’s sharing flags from the response. It returns true if the channel is already externally shared or pending external sharing, otherwise false.

**Call relations**: SlackConnectInviter._invite calls this after no live invite is found during reconciliation. It uses _call to get Slack’s channel information.

*Call graph*: calls 1 internal fn (_call).


##### `SlackConnectClient.invite_shared`  (lines 283–290)

```
async def invite_shared(self, channel_id: str, email: str) -> str
```

**Purpose**: This sends the actual Slack Connect invitation email for a channel. It also refuses email addresses that are too long before making the Slack request.

**Data flow**: It receives a channel ID and recipient email. It checks the email length, sends Slack’s conversations.inviteShared request, and extracts the invite ID from Slack’s response. The returned invite ID can then be saved in the database.

**Call relations**: SlackConnectInviter._invite calls this after it has decided it is safe to send a fresh invite. It relies on _call for the Slack request and _text to read the invite ID.

*Call graph*: calls 2 internal fn (_call, _text); 1 external calls (__init__).


##### `SlackConnectClient.redact`  (lines 292–293)

```
def redact(self, message: str) -> str
```

**Purpose**: This removes the bot token from an error message before the message is logged or stored. It also limits how much text is kept.

**Data flow**: It receives a message string, replaces any occurrence of the secret bot token with a fixed placeholder, cuts the result to the allowed length, and returns the safe text.

**Call relations**: The inviter uses this when saving retry or failure errors. This keeps secrets out of database rows and logs when Slack calls fail.


##### `SlackConnectClient._call`  (lines 295–323)

```
async def _call(self, method: str, params: dict[str, str | int]) -> dict[str, Any]
```

**Purpose**: This is the shared low-level helper for calling Slack’s Web API. It turns HTTP responses and Slack error codes into the project’s own temporary or permanent error types.

**Data flow**: It receives a Slack method name and request parameters. It sends an authenticated HTTP POST to Slack, drops empty parameter values, then inspects the HTTP status and Slack JSON response. On success it returns the response payload. On rate limits, server errors, transport failures, name conflicts, or other Slack errors, it raises the appropriate error.

**Call relations**: All higher-level SlackConnectClient methods use this rather than making HTTP requests directly. It calls _retry_after when Slack rate-limits the request, and its errors drive whether SlackConnectInviter._advance retries, fails, or recovers.

*Call graph*: calls 2 internal fn (__init__, _retry_after); called by 6 (channel_id_by_name, create_channel, invite_shared, is_externally_shared, outgoing_invite_id, team_id); 3 external calls (__init__, __init__, AsyncClient).


##### `SlackConnectClient._text`  (lines 325–331)

```
def _text(self, payload: dict[str, Any], *path: str) -> str
```

**Purpose**: This safely reads a text value from a nested Slack response. It prevents later code from silently accepting a malformed response.

**Data flow**: It receives a response dictionary and a path of keys to follow. It walks through the dictionary one key at a time. If any key is missing or the shape is wrong, it raises a permanent Slack error; otherwise it returns the found value as text.

**Call relations**: SlackConnectClient.team_id, create_channel, channel_id_by_name, outgoing_invite_id, and invite_shared use this after _call succeeds. It centralizes response validation for values the workflow depends on.

*Call graph*: called by 5 (channel_id_by_name, create_channel, invite_shared, outgoing_invite_id, team_id); 1 external calls (__init__).


##### `_retry_after`  (lines 334–338)

```
def _retry_after(response: httpx.Response) -> float | None
```

**Purpose**: This reads Slack’s retry-after header when Slack rate-limits the worker. The header tells the system how long to wait before trying again.

**Data flow**: It receives an HTTP response, looks for a numeric retry-after header, and returns that value capped at the project’s maximum allowed delay. If the header is missing or not a plain number, it returns nothing.

**Call relations**: SlackConnectClient._call uses this when Slack returns HTTP 429, which means too many requests. The resulting delay is carried inside SlackTransientError and later used by _reschedule.

*Call graph*: called by 1 (_call).


##### `SlackConnectInviter.run`  (lines 364–388)

```
async def run(self) -> None
```

**Purpose**: This is the forever-running background loop for Slack Connect deliveries. It keeps polling for work and deliberately stays alive even when one sweep fails.

**Data flow**: It starts with a failure counter at zero, repeatedly calls poll, and resets the counter after successful polling. If an unexpected error happens, it logs the failure and continues. If no row was claimed, it sleeps for the configured poll interval before checking again.

**Call relations**: The gateway lifespan can start this task when Slack Connect delivery is enabled. It calls poll for each sweep, and only cancellation stops the loop.

*Call graph*: calls 1 internal fn (poll); 1 external calls (sleep).


##### `SlackConnectInviter.poll`  (lines 390–405)

```
async def poll(self) -> bool
```

**Purpose**: This performs one sweep of the delivery queue. It creates any missing delivery rows, claims one due row, and advances that row through Slack work.

**Data flow**: It first materializes eligible signup claims into delivery rows. Then it tries to claim one pending or expired row. If none exists, it returns false. If it claims one, it starts a lease-renewal task, advances the delivery, cancels the renewal task, waits for cleanup, and returns true.

**Call relations**: SlackConnectInviter.run calls this repeatedly. Inside a sweep, it hands off to _materialize, _claim, _renew_lease, and _advance to separate database queue work from Slack delivery work.

*Call graph*: calls 4 internal fn (_advance, _claim, _materialize, _renew_lease); called by 1 (run); 2 external calls (create_task, gather).


##### `SlackConnectInviter._materialize`  (lines 407–421)

```
async def _materialize(self) -> None
```

**Purpose**: This adds delivery rows for completed signup claims that qualify for Slack Connect. It is how durable signup events become work items for the background poller.

**Data flow**: It opens a database transaction, takes a database advisory lock so only one replica inserts at a time, and runs the insert-from-select statement. Eligible claims become pending delivery rows, while conflicts are skipped instead of stopping the whole batch.

**Call relations**: SlackConnectInviter.poll calls this before claiming work. It protects the rest of the queue from one conflicting channel name and makes sure all replicas derive the same channel name from the database.

*Call graph*: called by 1 (poll).


##### `SlackConnectInviter._claim`  (lines 423–435)

```
async def _claim(self) -> _Delivery | None
```

**Purpose**: This leases one delivery row for the current worker. A lease is a time-limited claim that says, “this replica is working on this row right now.”

**Data flow**: It asks the database for one pending row that is due, or one previously claimed row whose lease expired. If a row is found, the database marks it claimed, records this worker ID, extends the lease expiry, increments the attempt count, and returns the row data. The function wraps that data in a _Delivery object. If no row is due, it returns nothing.

**Call relations**: SlackConnectInviter.poll calls this after materializing rows. The returned _Delivery is then passed to _advance, while _renew_lease keeps the claim alive during slow Slack calls.

*Call graph*: called by 1 (poll); 1 external calls (__init__).


##### `SlackConnectInviter._renew_lease`  (lines 437–459)

```
async def _renew_lease(self, onboard_claim_id: UUID) -> None
```

**Purpose**: This keeps a claimed delivery row reserved while Slack calls are still in progress. Without it, another replica might think the row was abandoned and send a duplicate invitation.

**Data flow**: It receives a signup claim ID and loops forever until cancelled. After each renewal delay, it updates the row’s lease expiry if this worker still owns it. Database errors are logged and retried; cancellation is allowed to stop the loop.

**Call relations**: SlackConnectInviter.poll starts this as a separate task after _claim succeeds and cancels it after _advance finishes. The actual delivery writes still check ownership through _write.

*Call graph*: called by 1 (poll); 1 external calls (sleep).


##### `SlackConnectInviter._advance`  (lines 461–488)

```
async def _advance(self, delivery: _Delivery) -> None
```

**Purpose**: This is the main step-by-step delivery path for one claimed row. It verifies configuration, opens or finds the Slack channel, sends or reconciles the invitation, and records the final result.

**Data flow**: It receives a _Delivery row. It verifies the Slack team, gets a channel ID either from the row or by opening a channel, then gets an invitation ID either from the row or by inviting/reconciling. Temporary Slack errors cause the row to be rescheduled. Permanent errors cause it to be marked failed. Success marks the row delivered and clears lease and retry fields.

**Call relations**: SlackConnectInviter.poll calls this after claiming a row. It delegates the major phases to _verify_team, _open_channel, and _invite, and uses _reschedule, _fail, and _write to record outcomes.

*Call graph*: calls 6 internal fn (_fail, _invite, _open_channel, _reschedule, _verify_team, _write); called by 1 (poll).


##### `SlackConnectInviter._verify_team`  (lines 490–498)

```
async def _verify_team(self) -> None
```

**Purpose**: This confirms the Slack bot token belongs to the expected operator workspace before any channel is changed. It prevents a misconfigured token from creating customer channels in the wrong Slack workspace.

**Data flow**: It asks the Slack client for the token’s team ID and compares it with the configured expected team ID. If they match, nothing is returned and delivery continues. If they differ, it raises a configuration error.

**Call relations**: SlackConnectInviter._advance calls this first on every delivery. It intentionally rechecks each time instead of caching, so token rotation mistakes are caught immediately.

*Call graph*: called by 1 (_advance); 1 external calls (__init__).


##### `SlackConnectInviter._open_channel`  (lines 500–506)

```
async def _open_channel(self, delivery: _Delivery) -> str
```

**Purpose**: This makes sure the delivery row has a Slack channel ID. It creates the deterministic channel if possible, or finds the existing channel if Slack says the name is already taken.

**Data flow**: It receives a _Delivery row. It tries to create the Slack channel named in the row. If Slack reports the name is taken, it looks up that exact name instead. It then writes the channel ID back to the database and returns it.

**Call relations**: SlackConnectInviter._advance calls this when the row does not already have a channel ID. It hands the database update to _write so lease ownership is checked before saving.

*Call graph*: calls 1 internal fn (_write); called by 1 (_advance).


##### `SlackConnectInviter._invite`  (lines 508–525)

```
async def _invite(self, delivery: _Delivery, channel_id: str) -> str | None
```

**Purpose**: This makes sure the customer has a Slack Connect invitation, while avoiding blind duplicate sends. It reconciles with Slack first if an earlier attempt may already have happened.

**Data flow**: It receives a _Delivery row and channel ID. If the row already has an invite-attempt timestamp, it checks for an existing live outgoing invite and then checks whether the channel is already externally shared. If either proves delivery, it records or returns that result. Otherwise it records that a new invite is being attempted, sends the invite email, saves the returned invitation ID, and returns it.

**Call relations**: SlackConnectInviter._advance calls this after a channel exists. It uses _write before the Slack invite call to make lost responses recoverable, and uses _persist_invitation to save a known invite ID.

*Call graph*: calls 2 internal fn (_persist_invitation, _write); called by 1 (_advance).


##### `SlackConnectInviter._persist_invitation`  (lines 527–529)

```
async def _persist_invitation(self, delivery: _Delivery, invitation_id: str) -> str
```

**Purpose**: This saves Slack’s invitation ID on the delivery row. That ID is proof that Slack accepted the invitation request.

**Data flow**: It receives the delivery row and the invitation ID. It writes the ID into the database for that claim and returns the same ID to the caller.

**Call relations**: SlackConnectInviter._invite calls this both when it finds an existing live invite and when it sends a new one. It delegates the guarded database update to _write.

*Call graph*: calls 1 internal fn (_write); called by 1 (_invite).


##### `SlackConnectInviter._reschedule`  (lines 531–550)

```
async def _reschedule(self, delivery: _Delivery, error: SlackTransientError) -> None
```

**Purpose**: This schedules a temporary failure for another try later. It uses Slack’s suggested delay when available, otherwise it uses increasing backoff delays.

**Data flow**: It receives the delivery row and a temporary Slack error. If the row has already reached the maximum attempt count, it marks the row failed. Otherwise it computes a delay, resets the row to pending, clears the worker and lease, stores the next attempt time and redacted error message, and logs the retry.

**Call relations**: SlackConnectInviter._advance calls this when SlackConnectClient raises a temporary error. If retries are exhausted, _reschedule hands off to _fail; otherwise it updates the row through _write.

*Call graph*: calls 2 internal fn (_fail, _write); called by 1 (_advance); 1 external calls (timedelta).


##### `SlackConnectInviter._fail`  (lines 552–563)

```
async def _fail(self, delivery: _Delivery, error: Exception) -> None
```

**Purpose**: This marks a delivery row as needing operator review. It is used for permanent Slack errors, exhausted retries, and unexpected failures.

**Data flow**: It receives the delivery row and an error. It redacts any bot token from the error text, writes the row as failed, clears the worker, lease, and next retry time, stores the safe error message, and logs the failure.

**Call relations**: SlackConnectInviter._advance calls this for permanent or unexpected errors. SlackConnectInviter._reschedule also calls it when a temporary problem has been retried too many times. The database write goes through _write.

*Call graph*: calls 1 internal fn (_write); called by 2 (_advance, _reschedule).


##### `SlackConnectInviter._write`  (lines 565–574)

```
async def _write(self, onboard_claim_id: UUID, assignment: str, *values: object) -> None
```

**Purpose**: This performs guarded database updates for a delivery row. It only writes if the current worker still owns the lease.

**Data flow**: It receives a signup claim ID, a SQL assignment fragment, and any extra values needed by that assignment. It updates the matching row only when the worker ID still matches this inviter, refreshes updated_at, and returns nothing. If no row was updated, it raises a lease-lost error.

**Call relations**: _advance, _open_channel, _invite, _persist_invitation, _reschedule, and _fail all use this for state changes. If another replica has taken over the row, the raised _LeaseLost stops the old worker from overwriting newer work.

*Call graph*: called by 6 (_advance, _fail, _invite, _open_channel, _persist_invitation, _reschedule); 1 external calls (__init__).


##### `slack_connect_from_env`  (lines 577–592)

```
def slack_connect_from_env(pool: asyncpg.Pool) -> SlackConnectInviter | None
```

**Purpose**: This builds the Slack Connect inviter from environment variables at gateway startup. It also allows the whole feature to be turned off cleanly.

**Data flow**: It receives a database pool and reads the enable switch from the environment. If disabled, it returns nothing. If enabled, it requires the bot token and expected team ID, creates a SlackConnectClient and SlackConnectInviter, assigns a worker ID from hostname and process ID, and returns the inviter. If the enable switch is not a clear true or false value, it raises an error.

**Call relations**: Startup code can call this to decide whether to launch SlackConnectInviter.run. It uses _require_env to fail early when required settings are missing.

*Call graph*: calls 1 internal fn (_require_env); 4 external calls (__init__, __init__, getpid, gethostname).


##### `_require_env`  (lines 595–599)

```
def _require_env(name: str) -> str
```

**Purpose**: This reads a required environment variable and fails clearly if it is missing. It prevents a half-configured Slack Connect deployment from starting.

**Data flow**: It receives the environment variable name, reads its value, and returns the value if present. If the value is missing or empty, it raises a runtime error explaining that the variable is required when Slack Connect is enabled.

**Call relations**: slack_connect_from_env calls this for the bot token and expected Slack team ID after the feature switch is enabled.

*Call graph*: called by 1 (slack_connect_from_env).


### `core/src/ufo/candidates.py`

`domain_logic` · `scheduler tick before job dispatch`

In this project, data is separated by workspace, like keeping each customer’s papers in a different locked drawer. Most code is only allowed to open one drawer at a time. But a background job first needs to know which drawers might contain work. This file provides that narrow exception.

The key idea is called a workspace candidate: a small async function that returns a tuple of workspace IDs. It does not return the actual rows of work, customer data, or task details. It only answers, “Which workspaces should the dispatcher visit?”

The function `owner_candidates` is the safe doorway for extensions. An extension supplies a builder function that creates a database query selecting workspace IDs from its own tables. Core code then runs that query through `owner_tx`, the special database path that can read across workspaces. This is deliberately kept inside core, so extensions do not get direct access to the cross-workspace connection.

A subtle but important detail is that the query is built each time candidates are requested. That means time-based checks, such as “due before now,” use the current time on each scheduler tick instead of freezing the time when the extension was loaded. After IDs are returned, the dispatcher is expected to re-enter each workspace normally before running the job.

#### Function details

##### `owner_candidates`  (lines 27–40)

```
def owner_candidates(due: Callable[[], sa.Select[tuple[UUID]]]) -> WorkspaceCandidates
```

**Purpose**: This function turns a query-building function into a workspace-candidate function. It gives extensions a safe way to say which workspaces have pending work without giving them direct access to the cross-workspace database connection.

**Data flow**: It receives `due`, a no-argument function that builds a database select query whose first column is a workspace ID. It wraps that builder inside an async `candidates` function. The result is a callable that, when run later, will execute the fresh query and return only the workspace IDs it finds.

**Call relations**: This is the public seam used when job or extension code needs to declare where work may exist. It creates and returns `owner_candidates.candidates`, which does the actual database read at scheduler time.


##### `owner_candidates.candidates`  (lines 35–38)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: This inner async function performs the actual candidate lookup. It reads across workspaces only long enough to collect workspace IDs, not the underlying work data.

**Data flow**: When called, it opens `owner_tx`, the special cross-workspace database transaction. Inside that transaction it calls `due()` to build a fresh query, executes it, collects all rows, and takes the first value from each row as a workspace ID. It closes the transaction and returns those IDs as a tuple.

**Call relations**: This function is the callable returned by `owner_candidates`. During a scheduler tick or dispatch preparation, core code can call it to learn which workspaces to visit. Its only direct handoff is to `ufo.db.owner_tx`, which supplies the special database connection used for this limited read.

*Call graph*: 1 external calls (owner_tx).


### `core/src/ufo/jobs.py`

`orchestration` · `startup and scheduled background work`

This file is the project’s background-job switchboard. At startup, the system has a list of job descriptions from the core app and installed extensions. This file registers those jobs with DBOS, a durable workflow system that stores queued work in the database so it can survive crashes and restarts. Without this layer, extension jobs would not be discovered dynamically, recurring work could be lost, and a job might accidentally run outside the workspace it belongs to.

The main pattern is “tick, fan out, then work.” A scheduled or one-time tick fires for a job. The tick asks the job which workspaces actually have pending work. It then queues one separate workflow for each workspace. This matters because one slow or stuck workspace should not block others, and duplicate ticks should not pile up multiple copies of the same job for the same workspace.

The file also defines core jobs that every deployment needs. One syncs source pages. One replays page changes to extension hooks, with a separate cursor for each hook so they do not trip over each other. One dispatches queued or parked conversation turns onto the turn-processing queue, while checking ordering, seat access, and spending limits. In short, this file is the reliable background-work coordinator for both core features and extensions.

#### Function details

##### `TurnDispatcher.run`  (lines 124–162)

```
async def run(self) -> None
```

**Purpose**: Looks for conversation turns that are ready to be sent to the turn worker queue. For parked turns, it first checks whether the needed people have seats and whether spending rules allow the turn to resume.

**Data flow**: It starts by reading dispatchable turn records. For each parked turn, it opens a workspace database transaction, gathers the members whose seats matter, checks seat admission and spend allowance, and skips the turn if either check fails. For turns that pass, or ordinary queued turns, it hands the turn to the enqueue step, which marks it and offers it to the worker queue.

**Call relations**: This is called by the core turn-dispatch job. It relies on _dispatchable_turns to find candidates and on _enqueue to make the queue offer safely, so the scan, gate checks, and actual queue handoff stay separated.

*Call graph*: calls 2 internal fn (_dispatchable_turns, _enqueue); 5 external calls (__init__, __init__, select, workspace_tx, gate_member).


##### `TurnDispatcher.candidate_workspaces`  (lines 164–172)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: Finds which workspaces have at least one turn that may need dispatching. This lets the job runner avoid opening workspaces that have no pending turn work.

**Data flow**: It computes a cutoff time for stale dispatch stamps, queries the owner-level database view for distinct workspace IDs with eligible turns, and returns those IDs as a tuple. It does not enqueue anything itself.

**Call relations**: The JobRunner asks this through the core turn-dispatch JobSpec before fanning out work. It uses _eligible to describe the same readiness rules that the per-workspace scanner later applies.

*Call graph*: calls 1 internal fn (_eligible); 4 external calls (now, timedelta, select, owner_tx).


##### `TurnDispatcher._dispatchable_turns`  (lines 174–213)

```
async def _dispatchable_turns(self) -> tuple[_DispatchTurn, ...]
```

**Purpose**: Loads a bounded batch of turn rows in the current workspace that are ready to be offered to workers. It preserves conversation order so a later turn cannot jump ahead of an earlier one.

**Data flow**: It computes the stale-stamp cutoff, queries the workspace database for queued or parked turns matching the eligibility rule, sorts them so queued turns and older turns are considered first, limits the count, and converts each database row into a small _DispatchTurn object.

**Call relations**: TurnDispatcher.run calls this at the start of a dispatch pass. It shares the _eligible rule with candidate_workspaces so the fleet-wide check and workspace-local scan agree.

*Call graph*: calls 1 internal fn (_eligible); called by 1 (run); 6 external calls (__init__, now, timedelta, case, select, workspace_tx).


##### `TurnDispatcher._enqueue`  (lines 215–245)

```
async def _enqueue(self, turn: _DispatchTurn) -> None
```

**Purpose**: Safely marks one turn as offered to the queue and then enqueues the worker workflow for it. The marking step prevents two dispatchers from both offering the same turn at the same time.

**Data flow**: It receives a _DispatchTurn, recalculates the stale cutoff, and tries to update the matching turn row only if it still has the same status, is still stale, and is still first in its conversation for that status. If the update succeeds, it builds queue options, chooses a workflow ID, and asks DBOS to enqueue the turn workflow. If the row was already claimed or changed, it returns without doing anything.

**Call relations**: TurnDispatcher.run calls this after any parked-turn checks pass. It uses _first_in_status and _stale to make the database update act like a lock, then hands the work to DBOS’s turn queue.

*Call graph*: calls 2 internal fn (_first_in_status, _stale); called by 1 (run); 5 external calls (now, timedelta, update, workspace_tx, uuid4).


##### `TurnDispatcher._eligible`  (lines 247–255)

```
def _eligible(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database rule for turns that may be dispatched. A turn is eligible only if it is queued or parked, its previous dispatch stamp is missing or old, and it is the first turn of its status in its conversation.

**Data flow**: It takes a cutoff time and combines smaller database conditions into one boolean SQL expression. The result is not a Python true-or-false answer yet; it is a filter used inside database queries.

**Call relations**: candidate_workspaces uses this to find workspaces with possible work, and _dispatchable_turns uses it to fetch the actual rows. It depends on _stale for retry timing and _first_in_status for conversation ordering.

*Call graph*: calls 2 internal fn (_first_in_status, _stale); called by 2 (_dispatchable_turns, candidate_workspaces); 2 external calls (and_, or_).


##### `TurnDispatcher._stale`  (lines 257–261)

```
def _stale(self, cutoff: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database rule for whether a turn’s dispatch offer is missing or old enough to retry. This lets the system recover if a process marked a turn but crashed before the worker claimed it.

**Data flow**: It takes a cutoff time and produces a SQL condition that matches rows whose dispatch_enqueued_at field is empty or earlier than the cutoff. Nothing is changed; the output is a query filter.

**Call relations**: _eligible uses this while scanning, and _enqueue uses it again during the final update. Rechecking it at enqueue time prevents racing dispatchers from acting on stale information.

*Call graph*: called by 2 (_eligible, _enqueue); 1 external calls (or_).


##### `TurnDispatcher._first_in_status`  (lines 263–272)

```
def _first_in_status(self, status: TurnStatus) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database rule that a turn must be the earliest turn of its status in its conversation. This protects the order of conversation turns.

**Data flow**: It takes a turn status such as queued or parked and creates a SQL condition saying “there is no earlier turn in the same workspace and conversation with this same status.” The result is used as part of larger queries and updates.

**Call relations**: _eligible uses this when finding possible turns, and _enqueue uses it just before offering a turn. This double use keeps both discovery and final claiming tied to the same ordering rule.

*Call graph*: called by 2 (_eligible, _enqueue); 2 external calls (exists, select).


##### `_page_beyond_cursor`  (lines 275–280)

```
def _page_beyond_cursor(revision: int, page_id: UUID, cursor: object) -> bool
```

**Purpose**: Answers whether a page change comes after a stored cursor. A cursor is like a bookmark that says how far a page-change consumer has already read.

**Data flow**: It receives a page revision, a page ID, and a stored cursor value. If there is no cursor, it returns true because everything is pending. Otherwise it parses the cursor into its saved revision and page ID, compares the new page position with that boundary, and returns true only if the page is later.

**Call relations**: PageChangeRunner.workspaces_with_changes uses this while deciding which workspaces have page changes waiting for a specific consumer. It delegates cursor parsing to the page_cursor helper from the source-sync code.

*Call graph*: called by 1 (workspaces_with_changes); 1 external calls (page_cursor).


##### `PageChangeRunner.consumers`  (lines 339–363)

```
def consumers(self) -> tuple[PageChangeConsumer, ...]
```

**Purpose**: Collects all registered page-change hooks from the active extension manifests. Each hook becomes a separate consumer with its own job name and cursor.

**Data flow**: It walks through every manifest, records the credential slots that extension declared, and selects only hooks whose event is page_change. It uses each handler function’s name as a discriminator, rejects duplicate names within the same extension, and returns PageChangeConsumer records.

**Call relations**: core_jobs calls this when building the always-on job list. The returned consumers are later used to create one scheduled page-change job per hook.

*Call graph*: called by 1 (core_jobs); 1 external calls (__init__).


##### `PageChangeRunner.workspaces_with_changes`  (lines 365–430)

```
async def workspaces_with_changes(self, consumer: PageChangeConsumer) -> tuple[UUID, ...]
```

**Purpose**: Finds the workspaces where one page-change consumer has unread page updates. This prevents the system from running that consumer in workspaces where its cursor is already caught up.

**Data flow**: It builds the cursor key for the consumer, reads stored cursors from the extension store, and reads each workspace’s newest page position. For each workspace, it compares the newest page with that consumer’s cursor. Workspaces with newer pages, missing cursors, or unparseable cursors are returned as pending; workspaces with no pages or no new pages are skipped.

**Call relations**: The page-change job’s candidate function calls this before JobRunner fans out work. It uses _page_beyond_cursor for the comparison and logs a warning if one workspace’s cursor cannot be parsed, without blocking the whole fleet.

*Call graph*: calls 1 internal fn (_page_beyond_cursor); 3 external calls (select, owner_tx, warn).


##### `PageChangeRunner.drive`  (lines 432–452)

```
async def drive(self, consumer: PageChangeConsumer) -> None
```

**Purpose**: Runs one page-change consumer inside the currently bound workspace. It feeds the consumer batches of changed pages and advances that consumer’s cursor only after the handler succeeds.

**Data flow**: It creates the extension context, reads the consumer’s stored cursor, and repeatedly asks the page feed for a batch after that cursor. For each non-empty batch, it calls the hook handler with a PageChangeBatch payload, then stores the next cursor. It stops when there are no changes or when the last batch is smaller than the batch limit.

**Call relations**: The handler returned by core_jobs._drive_consumer calls this. It uses _context_for to give the extension access to its scoped store and services, and it calls the extension’s hook handler with HookContext.

*Call graph*: calls 1 internal fn (_context_for); 2 external calls (__init__, __init__).


##### `PageChangeRunner._context_for`  (lines 454–470)

```
def _context_for(self, extension: str, declared: frozenset[str]) -> ExtensionContext
```

**Purpose**: Builds the ExtensionContext used by a page-change hook. This context is the hook’s toolbox: scoped storage, page access, optional model/index/blob/sandbox services, and job or turn invokers when available.

**Data flow**: It receives the extension name and declared credential slots. If an invoker factory exists, it creates an invoker for the current workspace. It then passes all available services into context_for and returns the resulting ExtensionContext.

**Call relations**: PageChangeRunner.drive calls this before invoking a hook. It uses the current workspace binding, so the context points at the workspace selected by the job runner rather than at a global scope.

*Call graph*: called by 1 (drive); 2 external calls (context_for, ws_current).


##### `core_jobs`  (lines 473–532)

```
def core_jobs(sync_driver: SyncDriver, turn_dispatcher: TurnDispatcher, page_change_runner: PageChangeRunner) -> tuple[JobSpec, ...]
```

**Purpose**: Creates the set of built-in jobs that every deployment should run. These include source syncing, page-change fan-out, and turn dispatch recovery.

**Data flow**: It receives the sync driver, turn dispatcher, and page-change runner. It creates small wrapper handlers and candidate functions, asks the page-change runner for its consumers, builds one JobSpec for each page-change consumer, and returns all core JobSpecs as a tuple.

**Call relations**: Startup code uses this before combining core jobs with extension jobs. The wrapper functions inside it connect generic JobSpec handlers to the concrete sync, dispatch, and page-change runner methods.

*Call graph*: calls 1 internal fn (consumers); 1 external calls (__init__).


##### `core_jobs._sync_sources`  (lines 489–490)

```
async def _sync_sources(context: ExtensionContext) -> None
```

**Purpose**: Adapts the source-sync driver to the job handler shape. The job system passes an ExtensionContext, but source syncing only needs to run the sync driver.

**Data flow**: It receives a context object and ignores it. It awaits sync_driver.run, which performs the actual source polling and page landing, and returns nothing.

**Call relations**: core_jobs places this function into the source-sync JobSpec. Later, JobRunner.fire calls it through that JobSpec when the source-sync job runs in a workspace.


##### `core_jobs._dispatch_turns`  (lines 492–493)

```
async def _dispatch_turns(context: ExtensionContext) -> None
```

**Purpose**: Adapts the turn dispatcher to the job handler shape. It lets turn dispatch run as a normal core job.

**Data flow**: It receives a context object and ignores it. It awaits turn_dispatcher.run, which scans and enqueues ready turns, then returns nothing.

**Call relations**: core_jobs places this function into the turn-dispatch JobSpec. JobRunner.fire later invokes it when a scheduled dispatch tick is fanned out to a workspace.


##### `core_jobs._drive_consumer`  (lines 495–501)

```
def _drive_consumer(consumer: PageChangeConsumer) -> Callable[[ExtensionContext], Awaitable[None]]
```

**Purpose**: Creates a job handler for one specific page-change consumer. This is how each hook gets its own scheduled job while sharing the same runner logic.

**Data flow**: It receives a PageChangeConsumer and returns an async handler function. The returned handler closes over that consumer, meaning it remembers which hook it is supposed to drive.

**Call relations**: core_jobs calls this while building page-change JobSpecs. The returned _handler is what JobRunner.fire eventually calls for that consumer’s page-change workflow.


##### `core_jobs._drive_consumer._handler`  (lines 498–499)

```
async def _handler(context: ExtensionContext) -> None
```

**Purpose**: Runs the page-change cursor loop for the consumer captured by core_jobs._drive_consumer. It is the actual JobSpec handler for that one page-change hook.

**Data flow**: It receives an ExtensionContext from the job runner, but the page-change runner builds the hook-specific context itself. It awaits page_change_runner.drive for the captured consumer and returns when that consumer is caught up or fails.

**Call relations**: JobRunner.fire calls this through a page-change JobSpec. It hands control to PageChangeRunner.drive, where pages are read, the hook is invoked, and the cursor is advanced.


##### `core_jobs._consumer_candidates`  (lines 503–507)

```
def _consumer_candidates(consumer: PageChangeConsumer) -> WorkspaceCandidates
```

**Purpose**: Creates the candidate-workspace function for one page-change consumer. It tells the job runner which workspaces have unread page changes for that hook.

**Data flow**: It receives a PageChangeConsumer and returns an async function that remembers that consumer. The returned function will later produce workspace IDs.

**Call relations**: core_jobs uses this when constructing each page-change JobSpec. JobRunner.tick later calls the returned _candidates function before queuing per-workspace work.


##### `core_jobs._consumer_candidates._candidates`  (lines 504–505)

```
async def _candidates() -> tuple[UUID, ...]
```

**Purpose**: Asks the page-change runner which workspaces need work for the captured consumer. This keeps page-change fan-out selective instead of running everywhere.

**Data flow**: It takes no direct inputs. It awaits page_change_runner.workspaces_with_changes for the captured consumer and returns the tuple of workspace IDs that still have pending page changes.

**Call relations**: JobRunner.tick calls this through the JobSpec candidate hook. Its output determines how many page-change workflows are enqueued for that tick.


##### `bindings_from`  (lines 543–568)

```
def bindings_from(manifests: tuple[Manifest, ...], core_jobs: tuple[JobSpec, ...]) -> tuple[_Binding, ...]
```

**Purpose**: Attaches each job to the namespace and credentials it should run with. Core jobs are placed under the core namespace, while extension jobs are placed under their extension’s namespace.

**Data flow**: It receives extension manifests and core JobSpecs. It creates _Binding records for every core job and every extension job, including the unique key, extension name, declared credential slots, and JobSpec. It returns all bindings as a tuple.

**Call relations**: Startup code uses this before creating a JobRunner. JobRunner later uses these bindings to register schedules, find candidate workspaces, and build the right ExtensionContext when firing a job.

*Call graph*: 1 external calls (__init__).


##### `JobRunner.launch`  (lines 591–615)

```
def launch(self) -> None
```

**Purpose**: Registers all known jobs with DBOS at startup. Recurring jobs become schedules, and one-time jobs are enqueued once with duplicate protection.

**Data flow**: It stores this JobRunner in the module-level _firing variable so DBOS workflow entrypoints can find it. It loops over every binding: jobs without schedules are enqueued immediately with a deduplication key, while scheduled jobs are collected as ScheduleInput records. Finally it applies all schedules to DBOS and logs what happened.

**Call relations**: This is the boot-time bridge between discovered JobSpecs and DBOS. Later, DBOS calls job_tick for scheduled fires or queued one-shots, and job_tick uses the _firing runner set here.

*Call graph*: 6 external calls (now, apply_schedules, ScheduleInput, SetEnqueueOptions, log, warn).


##### `JobRunner.tick`  (lines 617–639)

```
async def tick(self, scheduled_time: datetime, key: str) -> None
```

**Purpose**: Handles one firing of a job by spreading it across the workspaces that actually need it. It queues one durable workflow per candidate workspace.

**Data flow**: It receives the scheduled time and job key. If this process does not know that key, it logs a warning and stops. Otherwise it asks the job for candidate workspace IDs, then enqueues job_workflow once per workspace using a deduplication ID based on the job and workspace, skipping duplicates that are already running or queued.

**Call relations**: job_tick calls this when DBOS fires a schedule or one-shot tick. It calls candidates to find the fan-out targets and hands each target off to job_workflow through the jobs queue.

*Call graph*: calls 2 internal fn (_registered, candidates); 2 external calls (SetEnqueueOptions, warn).


##### `JobRunner.candidates`  (lines 641–642)

```
async def candidates(self, key: str) -> tuple[UUID, ...]
```

**Purpose**: Runs the candidate-workspace function for a registered job. This answers “where is there work to do?” before any actual handler runs.

**Data flow**: It receives a job key, resolves the matching binding, calls that JobSpec’s candidates function, and returns the workspace IDs it produces.

**Call relations**: JobRunner.tick calls this during fan-out. It uses _binding, which raises if the key is not registered, so callers should only use it for known jobs.

*Call graph*: calls 1 internal fn (_binding); called by 1 (tick).


##### `JobRunner.fire`  (lines 644–664)

```
async def fire(self, key: str, workspace_id: UUID) -> None
```

**Purpose**: Runs one job handler for one workspace. It is the only path that actually executes a job’s handler, and it always binds the workspace first.

**Data flow**: It receives a job key and workspace ID, resolves the binding, enters the workspace context, builds an ExtensionContext with the right services and credentials, and awaits the JobSpec handler. If the handler raises an error, it logs the failure and re-raises so the workflow is marked failed.

**Call relations**: job_workflow calls this after DBOS starts a per-workspace job execution. It uses _binding to find the job and context_for to build the handler’s environment.

*Call graph*: calls 1 internal fn (_binding); 3 external calls (context_for, log_error, ws).


##### `JobRunner._registered`  (lines 666–667)

```
def _registered(self, key: str) -> _Binding | None
```

**Purpose**: Looks up whether this process has a binding for a job key. It returns the binding if present, or nothing if the key belongs to an old, removed, or different-version job.

**Data flow**: It receives a key and scans the runner’s bindings for a matching binding.key. The output is either the _Binding record or None.

**Call relations**: JobRunner.tick uses this to skip stale schedules safely. JobRunner._binding also uses it when a missing binding should be treated as an error.

*Call graph*: called by 2 (_binding, tick).


##### `JobRunner._binding`  (lines 669–676)

```
def _binding(self, key: str) -> _Binding
```

**Purpose**: Returns the binding for a job key, or raises an error if the process cannot run that job. This is used once the code expects the key to be valid.

**Data flow**: It receives a key, asks _registered for the matching binding, and returns it if found. If no binding exists, it raises RuntimeError with the missing key.

**Call relations**: JobRunner.candidates and JobRunner.fire call this before using a JobSpec. JobRunner.tick uses _registered first because unknown scheduled keys are expected and should be skipped rather than treated as fatal.

*Call graph*: calls 1 internal fn (_registered); called by 2 (candidates, fire).


##### `job_tick`  (lines 683–687)

```
async def job_tick(scheduled_time: datetime, key: str) -> None
```

**Purpose**: DBOS workflow entrypoint for a job tick. It is the durable wrapper that DBOS calls when a schedule fires or a one-time job tick is queued.

**Data flow**: It receives the scheduled time and job key from DBOS. It reads the module-level _firing runner set during launch; if no runner exists, it raises an error. Otherwise it passes the scheduled time and key to runner.tick.

**Call relations**: JobRunner.launch registers or enqueues this workflow with DBOS. This function does not know job details itself; it delegates to the active JobRunner.


##### `job_workflow`  (lines 691–695)

```
async def job_workflow(scheduled_time: datetime, key: str, workspace_id: str) -> None
```

**Purpose**: DBOS workflow entrypoint for one job running in one workspace. It is the durable wrapper around the real per-workspace job execution.

**Data flow**: It receives the scheduled time, job key, and workspace ID as a string. It finds the active _firing runner, converts the workspace ID into a UUID, and calls runner.fire. The scheduled time is part of the workflow input but the actual firing logic uses the key and workspace.

**Call relations**: JobRunner.tick enqueues this workflow once for each candidate workspace. This function delegates to JobRunner.fire, which binds the workspace and invokes the actual handler.

*Call graph*: 1 external calls (UUID).


### Billing operations
Connects workspaces to Metronome, Stripe, and chat administration flows for durable usage billing, payment setup, and seat management.

### `extensions/metronome/ufo_ext_metronome.py`

`orchestration` · `scheduled jobs and chat tool handling`

This file is the billing bridge for a workspace. It does three main jobs. First, it ships settled usage records to Metronome, a billing and metering service. Each usage event gets a stable transaction ID, like a receipt number, so if the job crashes and sends the same event again, Metronome can recognize it as the same event instead of charging twice. Second, it sends a daily seat-count snapshot, so billing knows how many people had access. A missed day does not pile up into a permanent mistake because the next snapshot reports the current count again. Third, it lets workspace admins use chat tools to grant or revoke seats, list seat status, and set up billing.

Billing setup is split into two stages. The chat tool creates or reuses a Stripe Customer and returns a short-lived Stripe portal link where the admin can save a payment method. A scheduled activation job later checks Stripe, then creates the matching Metronome customer and contract. The code uses stable identities, such as the workspace ID and a fixed contract key, so retries reconcile with existing provider objects instead of making duplicates.

The manifest at the bottom tells the host system which tools, scheduled jobs, prompt instructions, and credential slots this extension provides.

#### Function details

##### `UsageShipper.run`  (lines 197–212)

```
async def run(self) -> None
```

**Purpose**: Sends one workspace's settled usage records to Metronome in batches. It is careful to acknowledge records only after Metronome accepts them, so a crash causes a safe resend rather than lost usage.

**Data flow**: It reads the Metronome bearer token from the environment, finds the workspace's fixed backfill floor, then asks the extension context for pending usage exports. It turns those exports into Metronome events, posts them, logs success, and marks the exports as acknowledged. It repeats until there is no more work or the final batch is smaller than the batch size.

**Call relations**: The scheduled `_ship` wrapper creates a `UsageShipper` and calls this method. During the run it relies on `_floor` to decide how far back to look, `_events` to shape records for Metronome, `_ingest` to send them, and `_require_env` to fail early if the token is missing.

*Call graph*: calls 4 internal fn (_events, _floor, _ingest, _require_env); 1 external calls (log).


##### `UsageShipper._floor`  (lines 214–224)

```
async def _floor(self) -> datetime
```

**Purpose**: Chooses the earliest usage time this workspace will ever ship. This prevents the first run from backfilling too far into the past while still letting old unshipped records be sent later.

**Data flow**: It reads a stored timestamp from the workspace store. If none exists, it creates one set to seven days before the current time, saves it, and returns it. If one already exists, it parses and returns that saved time unchanged.

**Call relations**: Only `UsageShipper.run` calls this before asking for pending usage exports. It acts like a fixed starting line for all later usage shipping in that workspace.

*Call graph*: called by 1 (run); 3 external calls (fromisoformat, now, timedelta).


##### `UsageShipper._events`  (lines 226–245)

```
def _events(self, exports: tuple[UsageExport, ...]) -> list[dict[str, object]]
```

**Purpose**: Converts internal usage export records into the event format Metronome expects. It preserves the important billing details, including model, amount, price, and whether the workspace used its own provider key.

**Data flow**: It takes a tuple of `UsageExport` objects and reads the workspace ID from the context. For each export, it builds a dictionary with a deterministic transaction ID, customer ID, timestamp, event type, and string-valued properties. The result is a list of event dictionaries ready to send.

**Call relations**: `UsageShipper.run` calls this immediately before `_ingest`. It uses `_rfc3339` so event timestamps are written in a standard internet date format.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run).


##### `_ship`  (lines 248–249)

```
async def _ship(ctx: ExtensionContext) -> None
```

**Purpose**: Acts as the scheduled job entry for usage shipping. It adapts the host job system's context into a `UsageShipper` run.

**Data flow**: It receives an extension context, creates a `UsageShipper` with that context and the configured test or production transport, and starts the shipper. It does not return a value beyond completing or raising an error.

**Call relations**: The extension `manifest` registers this as the usage job handler. Its whole job is to hand control to `UsageShipper.run`.

*Call graph*: 1 external calls (__init__).


##### `SeatShipper.run`  (lines 262–278)

```
async def run(self) -> None
```

**Purpose**: Sends Metronome one daily snapshot of how many seats a workspace is using. It also makes sure default seat limits and included seats exist before counting.

**Data flow**: It reads the Metronome token, checks whether today's snapshot was already marked as shipped, and exits if so. Otherwise it opens a transaction, ensures the workspace has a seat limit and included-seat count, reads the current seat snapshot, sends one Metronome event, logs it, and stores today's date as shipped.

**Call relations**: The scheduled `_ship_seats` wrapper creates a `SeatShipper` and calls this method. It calls `_event` to build the snapshot event, `_ingest` to send it, and `_require_env` to require the token.

*Call graph*: calls 3 internal fn (_event, _ingest, _require_env); 3 external calls (__init__, now, log).


##### `SeatShipper._event`  (lines 280–291)

```
def _event(self, snapshot: SeatSnapshot, today: str) -> dict[str, object]
```

**Purpose**: Builds the single Metronome event that represents today's seat count for a workspace.

**Data flow**: It receives a `SeatSnapshot` and today's date string. It reads the workspace ID, creates a stable transaction ID using workspace and date, records the current time, and includes the seated count and seat limit as event properties. It returns that event dictionary.

**Call relations**: `SeatShipper.run` calls this after reading the seat snapshot and before sending it through `_ingest`. It uses `_rfc3339` to format the timestamp.

*Call graph*: calls 1 internal fn (_rfc3339); called by 1 (run); 1 external calls (now).


##### `_ship_seats`  (lines 294–295)

```
async def _ship_seats(ctx: ExtensionContext) -> None
```

**Purpose**: Acts as the scheduled job entry for daily seat-count shipping.

**Data flow**: It receives an extension context, creates a `SeatShipper` with the configured transport, and runs it. Any provider error is allowed to surface so the scheduler can retry later.

**Call relations**: The `manifest` registers this as the seat shipping job handler. It simply hands the scheduled tick to `SeatShipper.run`.

*Call graph*: 1 external calls (__init__).


##### `SeatApprovals.run`  (lines 311–336)

```
async def run(self) -> None
```

**Purpose**: Creates chat-based approval requests when a workspace has unseated members but no included seats left. This lets admins decide whether to grant an overage seat without needing a separate approval screen.

**Data flow**: It reads the current seat snapshot. If included seats are unknown or not full, it stops. Otherwise it finds unseated members, skips anyone already asked about, finds an admin conversation, sends an internal prompt asking the admin to approve or decline, and then stores a marker so the same person is not asked again.

**Call relations**: The scheduled `_ask_seat_approvals` wrapper creates this class and runs it. It uses `Seats` to read seat state and `admin_conversation` to find where the approval request should be delivered.

*Call graph*: 3 external calls (__init__, now, admin_conversation).


##### `_ask_seat_approvals`  (lines 339–340)

```
async def _ask_seat_approvals(ctx: ExtensionContext) -> None
```

**Purpose**: Acts as the scheduled job entry for seat approval prompts.

**Data flow**: It receives an extension context, creates `SeatApprovals`, and runs it. The result is either no action or one or more admin-facing approval prompts.

**Call relations**: The `manifest` registers this as the seat approval job handler. It delegates the real decision-making to `SeatApprovals.run`.

*Call graph*: 1 external calls (__init__).


##### `BillingConfig.from_env`  (lines 359–378)

```
def from_env(cls) -> 'BillingConfig'
```

**Purpose**: Loads and validates the environment variables needed for billing setup and activation. It fails before making provider calls if any required setting is missing.

**Data flow**: It reads Stripe and Metronome settings from environment variables, collects all missing names, and raises an error if any are absent. If everything is present, it returns a frozen `BillingConfig` object with those values.

**Call relations**: Billing entry points call this before talking to Stripe or Metronome. It protects `_billing_setup`, `_billing_status`, `_billing_portal`, and `BillingActivation.run` from starting provider work with incomplete configuration.


##### `BillingActivation.run`  (lines 415–442)

```
async def run(self) -> None
```

**Purpose**: Turns a saved Stripe payment method into an active Metronome billing plan. It progresses step by step and records each completed provider object so retries resume safely.

**Data flow**: It reads the workspace billing record and exits if there is none or it is already activated. It loads billing config, asks Stripe whether a default payment method exists, creates or recovers a Metronome customer if needed, creates or recovers the workspace contract if needed, stores each new ID, and finally notifies the initiating conversation.

**Call relations**: The scheduled `_activate_billing` wrapper calls this. It coordinates `_billing_record`, `_has_default_payment_method`, `_metronome_customer`, `_metronome_contract`, `_contract_key`, `_store`, and `_notify` into one activation flow.

*Call graph*: calls 7 internal fn (_notify, _store, _billing_record, _contract_key, _has_default_payment_method, _metronome_contract, _metronome_customer).


##### `BillingActivation._store`  (lines 444–446)

```
async def _store(self, record: BillingRecord) -> BillingRecord
```

**Purpose**: Saves the current billing record for the workspace. It is used after each activation milestone so a later retry knows where to continue.

**Data flow**: It takes a `BillingRecord`, converts it to JSON-friendly data, writes it under the billing key in the extension store, and returns the same record.

**Call relations**: `BillingActivation.run` calls this after learning provider IDs. `_notify` calls it again after marking activation complete.

*Call graph*: called by 2 (_notify, run); 1 external calls (model_dump).


##### `BillingActivation._notify`  (lines 448–461)

```
async def _notify(self, record: BillingRecord) -> None
```

**Purpose**: Tells the original chat conversation that billing is now active, then marks the record as activated. This makes the confirmation happen once.

**Data flow**: It receives the billing record, sends an internal message to the stored conversation and agent using a stable idempotency key, updates the record with the current activation time, stores it, and writes a log entry.

**Call relations**: `BillingActivation.run` calls this after the Metronome contract exists. It uses `_store` to persist the final activated state.

*Call graph*: calls 1 internal fn (_store); called by 1 (run); 3 external calls (now, model_copy, log).


##### `_activate_billing`  (lines 464–465)

```
async def _activate_billing(ctx: ExtensionContext) -> None
```

**Purpose**: Acts as the scheduled job entry for billing activation.

**Data flow**: It receives an extension context, creates `BillingActivation` with the configured transport, and runs it. The job either advances pending billing setup or exits when there is nothing to do.

**Call relations**: The `manifest` registers this as the billing activation job handler. It delegates the workflow to `BillingActivation.run`.

*Call graph*: 1 external calls (__init__).


##### `_billing_record`  (lines 468–470)

```
async def _billing_record(ctx: ExtensionContext) -> BillingRecord | None
```

**Purpose**: Reads the workspace's stored billing setup state, if one exists.

**Data flow**: It looks up the billing key in the extension store. If nothing is stored, it returns `None`; otherwise it validates the stored data as a `BillingRecord` and returns that object.

**Call relations**: Billing activation and billing chat actions call this whenever they need to know whether setup has begun and which provider IDs are known.

*Call graph*: called by 4 (run, _billing_portal, _billing_setup, _billing_status).


##### `_contract_key`  (lines 473–477)

```
def _contract_key(workspace_id: UUID) -> str
```

**Purpose**: Creates the permanent Metronome contract identity for a workspace. This stable key lets the code find the same contract across retries and conflicts.

**Data flow**: It receives a workspace UUID and returns a string formed from a fixed prefix plus that UUID. It does not read or change anything else.

**Call relations**: `BillingActivation.run` uses this when creating a contract, and `_billing_status` uses it when checking whether the workspace's own contract exists.

*Call graph*: called by 2 (run, _billing_status).


##### `grant_seat`  (lines 515–521)

```
async def grant_seat(ctx: ToolContext, args: GrantSeatInput) -> ToolResult
```

**Purpose**: Chat tool handler that grants a workspace seat to a member by email. It is meant for admins and returns the updated seat picture.

**Data flow**: It verifies admin seat access through `_admin_seats`, opens a transaction, grants the seat, reads the new snapshot, and formats that snapshot as a tool result. The workspace seat state changes inside the transaction.

**Call relations**: The tool definition for `grant_seat` points to this handler. It relies on `_admin_seats` for permission checks and `_snapshot_result` for the response shown back to the agent.

*Call graph*: calls 2 internal fn (_admin_seats, _snapshot_result).


##### `revoke_seat`  (lines 524–534)

```
async def revoke_seat(ctx: ToolContext, args: RevokeSeatInput) -> ToolResult
```

**Purpose**: Chat tool handler that removes a member's seat by email. It also records that this member has already been decided on, so the approval job will not immediately ask about them again.

**Data flow**: It verifies admin seat access, opens a transaction, revokes the seat, reads the updated snapshot, writes a seat-approval marker for that email, and returns the snapshot as a tool result.

**Call relations**: The tool definition for `revoke_seat` points to this handler. It shares permission checking with `grant_seat` through `_admin_seats` and formats its answer through `_snapshot_result`.

*Call graph*: calls 2 internal fn (_admin_seats, _snapshot_result); 1 external calls (now).


##### `list_seats`  (lines 537–541)

```
async def list_seats(ctx: ToolContext, args: ListSeatsInput) -> ToolResult
```

**Purpose**: Chat tool handler that reports the current seat limit, included seats, overage count, and member seat status.

**Data flow**: It reads the workspace ID from the tool context, opens a transaction, asks `Seats` for a snapshot, and turns that snapshot into a JSON text tool result. It does not change seat state.

**Call relations**: The tool definition for `list_seats` points to this handler. It uses `_snapshot_result`, the same response formatter used by the seat-changing tools.

*Call graph*: calls 1 internal fn (_snapshot_result); 1 external calls (__init__).


##### `manage_billing`  (lines 544–553)

```
async def manage_billing(ctx: ToolContext, args: ManageBillingInput) -> ToolResult
```

**Purpose**: Chat tool handler for billing setup, billing status, and portal links. It gives admins one conversational doorway into Stripe and Metronome billing actions.

**Data flow**: It checks that the speaker can manage billing, loads billing configuration, then routes the requested action. `setup` starts or resumes payment setup, `status` reads provider state, and `portal` creates a management link.

**Call relations**: The tool definition for `manage_billing` points to this handler. It delegates permission checks to `_admin_billing` and then hands off to `_billing_setup`, `_billing_status`, or `_billing_portal`.

*Call graph*: calls 4 internal fn (_admin_billing, _billing_portal, _billing_setup, _billing_status).


##### `_admin_billing`  (lines 556–562)

```
async def _admin_billing(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Confirms that a billing tool call comes from a speaking workspace admin. This prevents ordinary members or system-only turns from changing billing.

**Data flow**: It reads the tool context, requires a speaker member ID, asks whether that speaker is an admin, and raises an error if not. If the speaker is allowed, it returns the extension context needed for billing work.

**Call relations**: `manage_billing` calls this before any billing action. It uses the context's `speaker_is_admin` check as the source of truth for permission.

*Call graph*: calls 1 internal fn (speaker_is_admin); called by 1 (manage_billing).


##### `_billing_setup`  (lines 565–605)

```
async def _billing_setup(ctx: ToolContext, ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Starts billing setup by creating or reusing the workspace's Stripe Customer and returning a Stripe portal link for saving a payment method.

**Data flow**: It reads any existing billing record. If none exists, it creates a Stripe Customer, records the intended Metronome package and contract start time, and stores the record before returning the link. It then creates a payment-method-update portal session and returns the URL, customer ID, and package as JSON text.

**Call relations**: `manage_billing` calls this for the `setup` action. It uses `_billing_record`, `_stripe_customer`, `_portal_session`, `_text_result`, and logging; the later `BillingActivation.run` job completes the plan after Stripe shows a saved payment method.

*Call graph*: calls 4 internal fn (_billing_record, _portal_session, _stripe_customer, _text_result); called by 1 (manage_billing); 3 external calls (__init__, now, log).


##### `_billing_status`  (lines 608–636)

```
async def _billing_status(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Reports the billing state as Stripe and Metronome currently see it. It avoids pretending a plan is active just because a local record exists.

**Data flow**: It reads the billing record. If there is none, it returns `configured: false`. Otherwise it asks Stripe whether a payment method is on file and, if there is a Metronome customer ID, asks Metronome whether the workspace's contract key exists. It returns those facts as JSON text.

**Call relations**: `manage_billing` calls this for the `status` action. It uses `_has_default_payment_method`, `_contract_for`, `_contract_key`, and `_text_result` to assemble the response.

*Call graph*: calls 5 internal fn (_billing_record, _contract_for, _contract_key, _has_default_payment_method, _text_result); called by 1 (manage_billing).


##### `_billing_portal`  (lines 639–647)

```
async def _billing_portal(ext: ExtensionContext, config: BillingConfig) -> ToolResult
```

**Purpose**: Creates a fresh Stripe Customer Portal link for an already set up workspace. Admins use this for invoices, billing details, and payment methods.

**Data flow**: It reads the billing record and raises an error if setup has not happened yet. If a record exists, it asks Stripe for a portal session with the stored customer ID and returns the URL as JSON text.

**Call relations**: `manage_billing` calls this for the `portal` action. It uses `_billing_record`, `_portal_session`, and `_text_result`.

*Call graph*: calls 3 internal fn (_billing_record, _portal_session, _text_result); called by 1 (manage_billing).


##### `_admin_seats`  (lines 650–655)

```
async def _admin_seats(ctx: ToolContext) -> Seats
```

**Purpose**: Confirms that a seat-changing tool call comes from a speaking workspace admin, then returns the seat helper for that workspace.

**Data flow**: It checks for a speaker member ID, asks whether the speaker is an admin, and raises an error if either condition fails. If allowed, it creates and returns a `Seats` object for the current workspace.

**Call relations**: `grant_seat` and `revoke_seat` call this before changing seats. It centralizes the permission rule so both tools enforce the same gate.

*Call graph*: calls 1 internal fn (speaker_is_admin); called by 2 (grant_seat, revoke_seat); 1 external calls (__init__).


##### `_snapshot_result`  (lines 658–672)

```
def _snapshot_result(snapshot: SeatSnapshot) -> ToolResult
```

**Purpose**: Turns a seat snapshot into the JSON text returned by seat tools. It presents both totals and per-member status in one response.

**Data flow**: It receives a `SeatSnapshot`, calculates billed overage seats when included seats are known, copies each member's email, seated flag, and admin flag, and passes the resulting dictionary to `_text_result`.

**Call relations**: `grant_seat`, `revoke_seat`, and `list_seats` all call this so their outputs have the same shape.

*Call graph*: calls 1 internal fn (_text_result); called by 3 (grant_seat, list_seats, revoke_seat).


##### `_text_result`  (lines 675–676)

```
def _text_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Wraps a dictionary as a text-based tool result. This is the common final packaging step for chat tool responses.

**Data flow**: It takes a dictionary, serializes it to a JSON string, places that string in a `TextContent` object, and returns a `ToolResult` containing that content.

**Call relations**: Billing and seat helper functions call this whenever they need to send structured data back through the tool system.

*Call graph*: called by 4 (_billing_portal, _billing_setup, _billing_status, _snapshot_result); 3 external calls (__init__, __init__, dumps).


##### `_require_env`  (lines 708–712)

```
def _require_env(name: str) -> str
```

**Purpose**: Reads one required environment variable and fails clearly if it is missing.

**Data flow**: It receives an environment variable name, looks it up, and returns the value if present. If the value is empty or absent, it raises a runtime error naming the missing setting.

**Call relations**: `UsageShipper.run` and `SeatShipper.run` call this before sending Metronome ingest events, because those jobs cannot work without the bearer token.

*Call graph*: called by 2 (run, run).


##### `_stripe_customer`  (lines 715–732)

```
async def _stripe_customer(config: BillingConfig, workspace_id: UUID, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Creates or reuses the workspace's Stripe Customer. A stable idempotency key helps Stripe treat retries as the same request.

**Data flow**: It receives billing config, a workspace ID, and an optional HTTP transport. It posts customer details and workspace metadata to Stripe, then extracts and returns the customer ID from the response.

**Call relations**: `_billing_setup` calls this when no billing record exists yet. It sends the actual HTTP request through `_stripe` and validates the returned ID with `_as_str`.

*Call graph*: calls 2 internal fn (_as_str, _stripe); called by 1 (_billing_setup).


##### `_portal_session`  (lines 735–751)

```
async def _portal_session(config: BillingConfig, customer_id: str, flow: str | None, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Creates a short-lived Stripe Customer Portal URL. Depending on the requested flow, the link can be limited to saving a payment method or can open the broader billing portal.

**Data flow**: It receives config, a Stripe customer ID, an optional flow name, and transport. It builds Stripe form data, posts it to the portal sessions endpoint, extracts the returned URL, and gives that URL back.

**Call relations**: `_billing_setup` calls this to make a payment-method setup link, and `_billing_portal` calls it to make a general management link. It uses `_stripe` for the provider request and `_as_str` to check the URL.

*Call graph*: calls 2 internal fn (_as_str, _stripe); called by 2 (_billing_portal, _billing_setup).


##### `_has_default_payment_method`  (lines 754–764)

```
async def _has_default_payment_method(config: BillingConfig, customer_id: str, transport: httpx.AsyncBaseTransport | None) -> bool
```

**Purpose**: Checks whether Stripe has a default payment method saved for the customer. This is the activation gate for turning billing into a live Metronome plan.

**Data flow**: It receives config, a Stripe customer ID, and transport. It fetches the customer from Stripe, looks inside invoice settings for a string default payment method, and returns true or false.

**Call relations**: `BillingActivation.run` uses this before creating Metronome billing objects. `_billing_status` uses it to report whether payment is ready.

*Call graph*: calls 1 internal fn (_stripe); called by 2 (run, _billing_status).


##### `_stripe`  (lines 767–785)

```
async def _stripe(config: BillingConfig, method: str, path: str, transport: httpx.AsyncBaseTransport | None, data: dict[str, str] | None=None, idempotency_key: str | None=None) -> dict[str, object]
```

**Purpose**: Performs one Stripe API request with the right authentication, pinned API version, timeout, and optional idempotency key.

**Data flow**: It receives config, HTTP method, path, transport, optional form data, and optional idempotency key. It sends the request to Stripe, raises `StripeError` on any non-success response, and returns the parsed JSON body on success.

**Call relations**: `_stripe_customer`, `_portal_session`, and `_has_default_payment_method` all use this shared Stripe transport helper so their error handling and headers stay consistent.

*Call graph*: called by 3 (_has_default_payment_method, _portal_session, _stripe_customer); 2 external calls (__init__, AsyncClient).


##### `_metronome_customer`  (lines 788–833)

```
async def _metronome_customer(config: BillingConfig, alias: str, stripe_customer_id: str, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Finds or creates the Metronome customer for a workspace. The workspace ID is used as an ingest alias so usage events attach to the right billing customer.

**Data flow**: It first looks for an existing Metronome customer by alias. If found, it returns that ID. If not, it posts a new customer with Stripe billing configuration. If Metronome reports a conflict, it looks up the alias again and returns the reconciled customer if present.

**Call relations**: `BillingActivation.run` calls this after Stripe has a payment method. It uses `_customer_by_alias` for lookup and `_metronome` for the provider requests.

*Call graph*: calls 2 internal fn (_customer_by_alias, _metronome); called by 1 (run); 1 external calls (__init__).


##### `_customer_by_alias`  (lines 836–845)

```
async def _customer_by_alias(config: BillingConfig, alias: str, transport: httpx.AsyncBaseTransport | None) -> str | None
```

**Purpose**: Looks up a Metronome customer by the workspace ingest alias.

**Data flow**: It receives config, an alias, and transport. It asks Metronome for customers matching that alias and returns the first customer ID if one is present; otherwise it returns `None`.

**Call relations**: `_metronome_customer` calls this before creating a customer and again after a conflict. The actual HTTP request goes through `_metronome`.

*Call graph*: calls 1 internal fn (_metronome); called by 1 (_metronome_customer).


##### `_metronome_contract`  (lines 848–884)

```
async def _metronome_contract(config: BillingConfig, customer_id: str, record: BillingRecord, uniqueness_key: str, transport: httpx.AsyncBaseTransport | None) -> str
```

**Purpose**: Finds or creates the workspace's Metronome contract, which represents the active billing plan. It uses a stable uniqueness key so retries point to the same contract.

**Data flow**: It receives config, customer ID, billing record, uniqueness key, and transport. It first checks whether the contract already exists. If not, it posts a contract create request using the recorded package and start time. On conflict, it looks up the contract again. It returns the contract ID or raises if the response has no ID.

**Call relations**: `BillingActivation.run` calls this after the Metronome customer is known. It uses `_contract_for` to read existing contracts, `_metronome` to create one, and `_rfc3339` to format the start time.

*Call graph*: calls 3 internal fn (_contract_for, _metronome, _rfc3339); called by 1 (run); 1 external calls (__init__).


##### `_contract_for`  (lines 887–910)

```
async def _contract_for(config: BillingConfig, customer_id: str, uniqueness_key: str, transport: httpx.AsyncBaseTransport | None) -> str | None
```

**Purpose**: Searches a Metronome customer's live contracts for the one that belongs to this workspace. It matches by the exact uniqueness key, not by whatever contract appears first.

**Data flow**: It receives config, a Metronome customer ID, a uniqueness key, and transport. It lists the customer's contracts, scans them, and returns the ID of the contract whose uniqueness key matches. If none match, it returns `None`.

**Call relations**: `_metronome_contract` uses this before and after create attempts. `_billing_status` uses it to decide whether the workspace's own plan is active.

*Call graph*: calls 1 internal fn (_metronome); called by 2 (_billing_status, _metronome_contract).


##### `_metronome`  (lines 913–933)

```
async def _metronome(config: BillingConfig, method: str, path: str, transport: httpx.AsyncBaseTransport | None, body: dict[str, object] | None=None, params: dict[str, str] | None=None, idempotency_key
```

**Purpose**: Performs one Metronome API request with the right authentication, timeout, JSON body, query parameters, and optional idempotency key.

**Data flow**: It receives config, HTTP method, path, transport, and optional request details. It sends the request, raises `MetronomeConflict` for HTTP 409 conflicts, raises `MetronomeError` for other failures, and returns the parsed JSON body on success.

**Call relations**: Metronome customer and contract helpers call this shared transport function. It keeps provider error handling consistent for `_customer_by_alias`, `_metronome_customer`, `_contract_for`, and `_metronome_contract`.

*Call graph*: called by 4 (_contract_for, _customer_by_alias, _metronome_contract, _metronome_customer); 3 external calls (__init__, __init__, AsyncClient).


##### `_as_str`  (lines 936–940)

```
def _as_str(value: object, field: str) -> str
```

**Purpose**: Checks that a provider response field is a non-empty string. It turns malformed provider responses into clear local errors.

**Data flow**: It receives any value and a human-readable field name. If the value is a non-empty string, it returns it; otherwise it raises a `ValueError` naming the missing field.

**Call relations**: `_stripe_customer` uses this for Stripe customer IDs, and `_portal_session` uses it for Stripe portal URLs.

*Call graph*: called by 2 (_portal_session, _stripe_customer).


##### `_ingest`  (lines 943–951)

```
async def _ingest(token: str, events: list[dict[str, object]], transport: httpx.AsyncBaseTransport | None) -> None
```

**Purpose**: Sends usage or seat events to Metronome's ingest endpoint. This is the final network step for metered events.

**Data flow**: It receives a bearer token, a list of event dictionaries, and an optional transport. It posts the list to Metronome with authorization. If Metronome rejects the request, it raises `MetronomeError`; otherwise it returns nothing.

**Call relations**: `UsageShipper.run` calls this for usage batches, and `SeatShipper.run` calls it for daily seat snapshots.

*Call graph*: called by 2 (run, run); 2 external calls (__init__, AsyncClient).


##### `_rfc3339`  (lines 954–956)

```
def _rfc3339(moment: datetime) -> str
```

**Purpose**: Formats a datetime as a standard timestamp string suitable for provider APIs. If the datetime has no timezone, it treats it as UTC.

**Data flow**: It receives a datetime. If it is timezone-aware, it uses it as-is; if not, it attaches UTC. It returns the ISO/RFC3339-style string form.

**Call relations**: `UsageShipper._events`, `SeatShipper._event`, and `_metronome_contract` call this when putting timestamps into Metronome requests.

*Call graph*: called by 3 (_event, _events, _metronome_contract); 1 external calls (replace).


##### `manifest`  (lines 959–1009)

```
def manifest() -> Manifest
```

**Purpose**: Declares everything this extension offers to the host system: tools, scheduled jobs, prompt guidance, and a credential slot. Without this, the host would not know how to run or expose the extension.

**Data flow**: It builds and returns a `Manifest` containing four chat tools, four scheduled jobs with their candidate workspace sets, two prompt sections for the agent, and one Anthropic API key credential slot. It does not perform billing work itself.

**Call relations**: The host loads this function to discover the extension. The job specs point to `_ship`, `_ship_seats`, `_ask_seat_approvals`, and `_activate_billing`; the tool definitions point to `grant_seat`, `revoke_seat`, `list_seats`, and `manage_billing`.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, metered_workspaces, member_workspaces).

## 📊 State Registers Touched

- `reg-database-schema` — The shared database layout and migration version that define which long-term records the system can store.
- `reg-workspace-boundary` — The current workspace or tenant boundary used to keep each customer’s data and actions separate.
- `reg-effective-configuration` — The chosen runtime settings that tell the service how this deployment should behave.
- `reg-pack-selection` — The selected product pack that decides which bundle of extensions, skills, and infrastructure is enabled.
- `reg-extension-inventory` — The installed extension set and their declared capabilities, such as tools, routes, jobs, skills, and storage.
- `reg-extension-store` — Per-workspace saved extension data that add-ons use to remember their own small pieces of state.
- `reg-model-catalog` — The shared list of available AI models, their limits, features, provider names, and calling rules.
- `reg-model-provider-adapters` — The shared provider clients that translate internal model requests into Anthropic, OpenAI, OpenRouter, or similar APIs.
- `reg-pricing-table` — The shared price list used to turn model and service usage into cost records.
- `reg-workspace-objects` — The shared records for workspaces, agents, members, conversations, artifacts, memories, sources, and other workspace objects.
- `reg-membership-and-seats` — The shared membership, admin role, paid seat, and seat-limit state for a workspace.
- `reg-agent-identity` — The saved identity and settings of each agent, including its main workspace role and whether it may use the internet.
- `reg-conversation-state` — The durable conversation record that ties a surface, agent, audience, sandbox handle, and message history together.
- `reg-turn-queue` — The durable queue of conversation turns waiting to be claimed, run, completed, cancelled, or retried.
- `reg-source-sync-state` — The saved state of external sources, synced pages, deletion markers, cursors, and retry backoff.
- `reg-surface-installations` — The saved Slack, web, terminal, and other surface bindings used to receive messages and send replies back.
- `reg-background-jobs` — The shared registry and saved queue of scheduled, recurring, delayed, and administrative background work.
- `reg-fleet-presence` — The shared record of live runtime processes used for supervision, cancellation, and recovery after crashes.
- `reg-accounting-ledger` — The shared usage ledger that records tokens, egress, sandbox usage, billing exports, and spend-limit checks.
- `reg-observability-context` — The shared logging, metrics, tracing, and trace-link state used to understand work across requests and subagents.
- `reg-proposal-governance` — The saved proposals and safety checks used to govern prompt or system improvements before applying them.
- `reg-surface-delivery-state` — Durable outbound reply/writeback state used to de-duplicate, track, retry, and complete delivery of responses to Slack, web, terminal, or other surfaces.
- `reg-evaluation-replay-state` — Saved evaluation corpora, replay fixtures, prompt-candidate runs, scores, and comparison results used before self-improvement proposals are governed.
- `reg-prompt-template-state` — The canonical prompt and instruction templates, versions, and digests used to assemble model prompts and guard prompt-improvement proposals against stale edits.
- `reg-outbound-http-client-pool` — Shared outbound HTTP client/session pool state used for connection reuse, retries, and provider/API calls across model adapters, connectors, search, tools, and billing jobs.
- `reg-acting-principal-scope` — The current acting principal context—member, agent, on-behalf-of member, and object/agent scope—used to authorize actions, attribute turns, choose grants, and keep tool work tied to the right actor.
- `reg-evaluation-environment-state` — Persisted synthetic evaluation-environment data, such as fake email and calendar records, used by evaluation connectors and replay/test workflows without touching real external services.
