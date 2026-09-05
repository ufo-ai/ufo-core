# Offline evaluation and improvement loops  `stage-15.2`

This stage is behind-the-scenes support for improving the agent after real use. It does not change live conversations directly. Instead, it studies past mistakes, tries safer alternatives offline, and only suggests changes when there is strong evidence.

The evaluation environment package sets up fake email and calendar services, so tests can run the same way every time without touching real accounts. The corpus builder scans old workspace conversations, finds cases where tools failed, groups them by failing tool, and splits them into learning and test examples. The model wrapper gives the rest of the code a simple way to ask the AI for text or for a tool-aware chat turn.

The proposer asks the AI to rewrite an agent’s system prompt, meaning its standing instructions, for a problem area. Replay then reruns saved conversations with the new prompt while reusing old tool results, like watching a recording with a different narrator. Evaluation compares old and new prompts on those replays. The gate applies cautious rules to decide if the new prompt is truly better. Finally, the cron job runs this process on a schedule and opens a human-reviewed proposal only after repeated success.

## Files in this stage

### Deterministic evaluation environment
Package setup for side-effect-free evaluation connectors used by tests and offline runs.

### `extensions/eval_env/ufo_ext_eval_env/__init__.py`

`other` · `evaluation setup`

This is the package entry file for the evaluation environment extension. Its main job is to identify this folder as a Python package and explain, through its short module note, what the package is for. The package contains deterministic stand-ins for mailbox and calendar connectors. “Deterministic” means they behave the same way every time they are used, like a practice inbox or calendar that always contains the same messages and events. This matters because real services can change, fail, be slow, or contain private data. For evaluation, the project needs a safe and repeatable setting where behavior can be checked fairly. Without this package marker, Python code elsewhere may not be able to import the fake connector providers in the normal way. Without the fake providers themselves, evaluations would have to touch real accounts or rely on unstable outside systems.


### Failure corpus construction
Builds train and test evaluation examples from past conversations where tools or tasks failed.

### `extensions/self_improvement/ufo_ext_self_improvement/corpus.py`

`domain_logic` · `self-improvement corpus building`

This file helps the self-improvement system find useful lessons in old transcripts. Since the system does not have a separate way for users or tools to say “this went badly,” it treats a tool error inside a conversation as the signal that something needs refinement. Think of it like a mechanic reviewing repair logs: the interesting cases are the ones where a part failed, and the cases are grouped by which part failed.

The file defines two simple frozen data shapes. A TaskExample is one useful failed conversation, reduced to the conversation ID, the user’s original request, the full message history, and a plain description of the tool problem. A TaskClass is a group of those examples for one kind of failure, named like “tool:search” or “tool:browser”. Each class is split into two piles: “mine,” which can be used to propose improvements, and “held_out,” which is saved for later replay and grading. This matters because testing on the same examples used to create a fix would make the fix look better than it really is.

The main flow scans each trajectory, finds the first user request and first tool error, builds a TaskExample when both exist, groups examples by failed tool, drops groups that are too small, and returns the remaining classes sorted by size.

#### Function details

##### `first_request`  (lines 39–43)

```
def first_request(messages: tuple[Message, ...]) -> str | None
```

**Purpose**: Finds the first real user request in a conversation. The self-improvement loop needs this request as the thing to grade against later: what was the user actually asking for?

**Data flow**: It receives the conversation’s messages. It scans them in order until it finds a message from the user whose content is plain non-empty text, then returns that text. If no such user message exists, it returns nothing.

**Call relations**: When bad_trajectory is deciding whether a past conversation is useful, it calls first_request to get the original user goal. If there is no request, the conversation cannot become a training or evaluation example.

*Call graph*: called by 1 (bad_trajectory).


##### `first_tool_error`  (lines 46–65)

```
def first_tool_error(messages: tuple[Message, ...]) -> tuple[str, str] | None
```

**Purpose**: Finds the first failed tool call in a conversation and identifies which tool failed. This is the main “friction signal” used to decide what kind of task the conversation belongs to.

**Data flow**: It receives the conversation’s messages. First it records which tool-use ID belongs to which tool name. Then it scans again for the first tool result marked as an error, matches it back to the tool name, and returns the tool name plus the error text. If no tool error can be matched, it returns nothing.

**Call relations**: bad_trajectory calls this before building an example. The result decides both whether the trajectory is worth keeping and which task class it belongs to, such as a class for failures of a particular tool.

*Call graph*: called by 1 (bad_trajectory).


##### `bad_trajectory`  (lines 68–78)

```
def bad_trajectory(trajectory: Trajectory) -> tuple[str, TaskExample] | None
```

**Purpose**: Turns one conversation into a self-improvement example if it contains both a user request and a tool error. It labels the example by the failed tool, so similar failures can be studied and tested together.

**Data flow**: It receives a Trajectory, which contains the conversation ID and all messages. It asks first_tool_error for the failed tool and error text, and first_request for the user’s original request. If either is missing, it returns nothing. If both exist, it builds a TaskExample containing the useful pieces and returns it together with a class name like “tool:<name>”.

**Call relations**: task_classes calls bad_trajectory for each stored trajectory. Inside, bad_trajectory relies on first_request and first_tool_error to extract the two facts that make a conversation useful: what the user wanted and what tool failure got in the way.

*Call graph*: calls 2 internal fn (first_request, first_tool_error); called by 1 (task_classes); 1 external calls (__init__).


##### `task_classes`  (lines 81–94)

```
def task_classes(trajectories: tuple[Trajectory, ...]) -> tuple[TaskClass, ...]
```

**Purpose**: Builds the final set of task classes from many past conversations. It groups failed conversations by the tool that failed, keeps only groups large enough to split fairly, and returns them in a stable useful order.

**Data flow**: It receives a tuple of trajectories. For each one, it asks bad_trajectory whether the conversation is a usable failure example. Usable examples are collected under their class name. Each group is passed to _split, and groups that are too small are dropped. The remaining TaskClass objects are sorted so larger classes come first, with names used to break ties, and returned as a tuple.

**Call relations**: This is the main entry point of this file’s logic. It coordinates the lower-level extraction done by bad_trajectory and the train-versus-test split done by _split, producing the corpus that later self-improvement steps can mine and replay.

*Call graph*: calls 2 internal fn (_split, bad_trajectory).


##### `_split`  (lines 97–102)

```
def _split(name: str, examples: tuple[TaskExample, ...]) -> TaskClass | None
```

**Purpose**: Divides one group of examples into examples to learn from and examples to hold back for grading. This prevents the system from judging an improvement only on the same conversations that inspired it.

**Data flow**: It receives a class name and that class’s examples. If there are not enough examples to make both a mining set and a held-out set, it returns nothing. Otherwise it sorts examples by conversation ID for repeatable results, chooses a held-out count, and returns a TaskClass with the early examples held out and the rest available for mining.

**Call relations**: task_classes calls _split after it has grouped examples by failed tool. _split creates the TaskClass object that downstream self-improvement code can use, with separate piles for proposing changes and evaluating them.

*Call graph*: called by 1 (task_classes); 1 external calls (__init__).


### Prompt candidate generation
Wraps model access and uses it to propose safer prompt revisions from observed failure patterns.

### `extensions/self_improvement/ufo_ext_self_improvement/model.py`

`io_transport` · `active whenever the extension asks the model to propose, replay, or grade work`

The self-improvement extension needs to call a language model in several places, such as proposing changes, replaying behavior, and grading results. Without this file, each part of the extension would need to know the details of how to build model requests, set token limits, attach tools, and use the SDK’s metered model access. That would spread fragile plumbing across the codebase.

This file acts like a small service counter between the extension and the model SDK. The rest of the extension can say either “complete this conversation and give me text” or “take a chat turn and maybe use these tools.” The adapter then packages the request in the shape the SDK expects.

It sets a shared output limit of 2048 tokens, uses the current workspace-selected model, keeps a short conversation cache for five minutes, and turns model reasoning mode off. The cache is like leaving a recent folder open on the desk so repeated related calls can be cheaper or faster, depending on the SDK behavior.

The file also defines two protocol types, ModelLeg and ReplayLeg. A protocol is a promise about what methods an object must provide. This makes it easy to swap in a real model, a test double, or another implementation as long as it follows the same small interface.

#### Function details

##### `ModelLeg.complete`  (lines 13–13)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This defines the simple promise for anything that can ask the model for a plain text completion. Code can depend on this small shape instead of depending on the full SDK model object.

**Data flow**: The caller provides a system instruction and a tuple of chat messages. An implementation is expected to send those to a model and return the model’s text answer as a string. This protocol method itself does not do the work; it describes the required behavior.

**Call relations**: Other parts of the extension can be written against ModelLeg when they only need text back from the model. ModelAccessLeg.complete is the real adapter in this file that fulfills this promise using the SDK.


##### `ReplayLeg.turn`  (lines 17–19)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This defines the simple promise for anything that can ask the model to produce the next chat message while tools are available. It is useful for replay-style flows where the model may need to choose or refer to tool use.

**Data flow**: The caller provides a system instruction, prior chat messages, and the available tool descriptions. An implementation is expected to send that package to a model and return one Message object representing the model’s next turn. This protocol method only states the contract; it does not perform the call itself.

**Call relations**: Replay flows can depend on ReplayLeg without caring which concrete model backend is used. ModelAccessLeg.turn is the concrete implementation here that turns this narrow request into an SDK ModelRequest.


##### `ModelAccessLeg.complete`  (lines 28–38)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This sends a text-only model request through the SDK’s metered ModelAccess object. It is used when the extension wants the model’s answer as a string rather than a full chat message with tool behavior.

**Data flow**: It receives a system prompt and the conversation messages. It builds a ModelRequest using the configured SDK model name, the shared 2048-token output limit, a five-minute conversation cache, and reasoning turned off. It then passes that request to the SDK model object and returns the text response it gets back.

**Call relations**: This method is the concrete version of the ModelLeg.complete promise. When higher-level proposing or grading code needs plain model text, it can call this adapter; the adapter creates the SDK ModelRequest and hands it to ModelAccess.complete.

*Call graph*: 1 external calls (__init__).


##### `ModelAccessLeg.turn`  (lines 40–53)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This sends a tool-aware chat turn request through the SDK’s metered ModelAccess object. It is used when the extension needs the model to produce a full Message, especially in replay situations where tools may be part of the conversation.

**Data flow**: It receives a system prompt, prior messages, and tool schemas that describe the tools the model may use. It wraps them in a ModelRequest with the selected model, the 2048-token output limit, a five-minute cache, and reasoning turned off. It sends that request to the SDK model object and returns the resulting Message.

**Call relations**: This method is the concrete version of the ReplayLeg.turn promise. Replay-oriented code can call this narrow interface, while this adapter handles the SDK-specific request construction and hands the work to ModelAccess.turn.

*Call graph*: 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/proposer.py`

`domain_logic` · `self-improvement proposal phase`

This file is part of a self-improvement loop. Its job is to take evidence that an agent had trouble with a recurring type of request, then ask another model to suggest a careful improvement to the agent’s system prompt. A system prompt is the instruction text that sets the agent’s behavior, style, and boundaries.

The key idea is modest improvement, not total reinvention. The proposer is told to preserve the existing prompt’s voice and scope, and to make the smallest change that would help with the problem examples. This matters because an over-specific rewrite could make the agent better at one task class while accidentally harming everything else it does.

The main class, PromptProposer, receives the current prompt and a TaskClass, which contains mined examples of requests and the problems seen with them. It formats those examples into a clear message, sends that message to a model, cleans up the model’s reply, and returns a PromptCandidate if the reply is non-empty and actually different from the current prompt.

There is also a small cleanup step because models sometimes wrap answers in Markdown code fences, like putting a note inside a decorative box. Since the rest of the system needs the raw prompt text, those wrappers are stripped away before the candidate is returned.

#### Function details

##### `PromptProposer.propose`  (lines 33–43)

```
async def propose(self, current_prompt: str, task_class: TaskClass) -> PromptCandidate | None
```

**Purpose**: This is the main action in the file: it tries to produce a better system prompt for one task class the agent struggled with. Someone would use it when they have the current prompt plus examples of failures or friction, and want a possible revised prompt to evaluate.

**Data flow**: It receives the current system prompt and a task class. If the task class has no mined problem examples, it stops immediately and returns nothing. Otherwise, it builds a user message with the current prompt and examples, sends that to the model with instructions for how to revise the prompt, cleans the model’s answer, checks that the answer is not empty and not identical to the current prompt, and then returns a PromptCandidate containing the task class name and the proposed prompt. If the model gives no useful new text, it returns nothing.

**Call relations**: This function drives the whole proposal flow. It calls PromptProposer._prompt to turn the task class and current prompt into a clear request for the model. It wraps that request in a Message before asking the model to complete it. After the model answers, it calls _clean so later steps receive plain prompt text. If the result is useful, it creates a PromptCandidate for the rest of the self-improvement system to consider.

*Call graph*: calls 2 internal fn (_prompt, _clean); 2 external calls (__init__, __init__).


##### `PromptProposer._prompt`  (lines 45–56)

```
def _prompt(self, current_prompt: str, task_class: TaskClass) -> str
```

**Purpose**: This helper builds the exact text sent to the model as the user’s request. It explains the task class, shows the current system prompt, and includes a limited set of examples that illustrate what went wrong.

**Data flow**: It receives the current prompt and a task class. It takes up to the configured maximum number of mined examples, trims each request and problem description to the configured character limit, and formats them as numbered examples. It then combines the task class name, current system prompt, examples, and final instruction into one plain text prompt. The output is a single string ready to send to the model.

**Call relations**: PromptProposer.propose calls this when it needs to prepare the model request. This function does not talk to the model itself; it only packages the information so the model has enough context to suggest a focused prompt revision.

*Call graph*: called by 1 (propose).


##### `_clean`  (lines 59–68)

```
def _clean(text: str) -> str
```

**Purpose**: This helper turns the model’s raw answer into plain prompt text. It removes surrounding whitespace and, if the model wrapped the answer in Markdown code fences, removes those fences too.

**Data flow**: It receives the text returned by the model. It strips leading and trailing whitespace. If the remaining text starts with a code fence marker, it removes the opening fence and a matching closing fence if present, then strips whitespace again. The result is the cleaned prompt body as a string.

**Call relations**: PromptProposer.propose calls this after the model replies. Its cleaned result is what propose compares against the current prompt and, if useful, stores in a PromptCandidate. This keeps accidental formatting from being mistaken for part of the agent’s actual system prompt.

*Call graph*: called by 1 (propose).


### Conservative prompt evaluation
Replays old conversations without side effects, grades prompt alternatives, and gates adoption with conservative evidence checks.

### `extensions/self_improvement/ufo_ext_self_improvement/evaluation.py`

`domain_logic` · `self-improvement evaluation`

This file is the evidence-gathering step for self-improvement. When the system invents a candidate prompt, it cannot simply trust that the prompt sounds better. It must test it on real examples and compare it fairly against the existing prompt.

The main object, CandidateEvaluation, does that comparison. For each saved task, it runs two versions of the agent: one using the current prompt and one using the candidate prompt. Both runs use the same archived task setup, so the prompt is the main thing being compared. Think of it like testing two recipes with the same ingredients and oven, then asking the same judge to taste both.

After each replay, the file asks a judge model whether the final answer satisfies the original user request. The judge is instructed to return a small JSON result, such as {"accepted": true}. If the judge gives malformed output, the answer is treated as not accepted. This is deliberately strict, because unclear grading would make the improvement decision unsafe.

The results are turned into OutcomeLabel records, which say whether the candidate prompt was present and whether the answer succeeded. Finally, the file hands those labels to the two-stage gate. That gate checks both local improvement on the target task class and global safety on other held-out tasks, so a prompt should not be accepted if it helps one area while hurting others.

#### Function details

##### `CandidateEvaluation.evaluate`  (lines 24–33)

```
async def evaluate(self, candidate_prompt: str, current_prompt: str, local_held_out: tuple[TaskExample, ...], global_held_out: tuple[TaskExample, ...]=()) -> GateVerdict
```

**Purpose**: This is the main entry point for testing a candidate prompt. It compares the candidate prompt against the current prompt on local held-out examples, optionally checks broader global examples, and returns the final gate decision.

**Data flow**: It receives the candidate prompt, the current prompt, and two sets of saved task examples. It first turns the local examples into success/failure labels for both prompts, then does the same for the global examples. It sends both groups of labels into the gate, which returns a GateVerdict saying whether the candidate should pass.

**Call relations**: When the self-improvement flow needs proof that a new prompt is worth adopting, it calls this method. This method relies on CandidateEvaluation._labels to produce the raw comparison data, then hands that data to two_stage_gate to make the accept-or-reject decision.

*Call graph*: calls 1 internal fn (_labels); 1 external calls (two_stage_gate).


##### `CandidateEvaluation._labels`  (lines 35–45)

```
async def _labels(self, candidate_prompt: str, current_prompt: str, held_out: tuple[TaskExample, ...]) -> tuple[OutcomeLabel, ...]
```

**Purpose**: This helper runs the fair side-by-side test for a group of saved examples. For every task, it tries both the current prompt and the candidate prompt, then records whether each answer was accepted.

**Data flow**: It receives the candidate prompt, the current prompt, and a tuple of held-out task examples. For each example, it creates a replay runner, replays the task once with the current prompt and once with the candidate prompt, asks whether each final answer is acceptable, and collects OutcomeLabel records. It returns those records as an immutable tuple.

**Call relations**: CandidateEvaluation.evaluate calls this once for local examples and once for global examples. Inside the loop, this method uses ReplayEvaluation to regenerate an answer from saved task messages, then calls CandidateEvaluation._accepts to grade that answer before packaging the result as an OutcomeLabel.

*Call graph*: calls 1 internal fn (_accepts); called by 1 (evaluate); 2 external calls (__init__, __init__).


##### `CandidateEvaluation._accepts`  (lines 47–59)

```
async def _accepts(self, request: str, answer: str) -> bool
```

**Purpose**: This helper asks the judge model whether one answer correctly satisfies one request. It turns the judge's JSON response into a simple true or false result.

**Data flow**: It receives the original user request and the answer produced by replay. It sends both to the judge model with grading instructions, then looks for a JSON object in the judge's text. If the JSON can be parsed and contains accepted set to true, it returns true; otherwise, including malformed judge output, it returns false.

**Call relations**: CandidateEvaluation._labels calls this after each replayed answer is produced. This method creates the user-facing judge message, asks the configured judge model for a verdict, and uses json.loads to read the JSON so the rest of the evaluation can work with a plain boolean success value.

*Call graph*: called by 1 (_labels); 2 external calls (__init__, loads).


### `extensions/self_improvement/ufo_ext_self_improvement/gate.py`

`domain_logic` · `self-improvement evaluation before prompt promotion`

This file is the safety gate for prompt self-improvement. Imagine testing a new recipe against the old one: it is not enough for the new recipe to win a few lucky taste tests. You want enough tastings, and you want the win to be big enough that it probably was not random chance. That is what this code does for candidate prompts.

Each replayed example is recorded as an OutcomeLabel: whether the candidate prompt was present, and whether the answer was accepted. The code first turns those labels into a Contingency count: accepted and total examples for the candidate side, and accepted and total examples for the current-prompt side.

The main local check asks: how much better is the candidate on the specific task class it was meant to improve? It computes a cautious lower estimate of the acceptance-rate lift, meaning the candidate acceptance rate minus the old acceptance rate, after allowing for uncertainty from small sample sizes. The candidate must have enough examples on both sides and its conservative improvement estimate must clear a minimum floor.

There is also a wider safety check. Even if the candidate helps one task class, it must not clearly hurt other task classes. This global check only blocks when the evidence says the candidate is meaningfully worse, not merely when the data is too small or noisy. The final two-stage gate combines both checks.

#### Function details

##### `wilson_lower_bound`  (lines 53–60)

```
def wilson_lower_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: This function gives a cautious low estimate of a success rate from a number of accepted examples and total examples. It is used when the code wants to avoid being fooled by a small lucky sample.

**Data flow**: It receives an accepted count, a total count, and optionally a confidence setting. If there are no examples, it returns 0. Otherwise it calculates the Wilson lower confidence bound, which is a statistically cautious version of the observed success rate, and returns that number between 0 and 1.

**Call relations**: The lift calculations call this when they need the pessimistic side of an acceptance rate. It uses square root math as part of the uncertainty calculation, then hands the bound back to lift_lower_bound or lift_upper_bound.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `wilson_upper_bound`  (lines 63–70)

```
def wilson_upper_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: This function gives a cautious high estimate of a success rate from accepted and total examples. It is useful when asking, 'How good could this side plausibly be, given the data?'

**Data flow**: It receives an accepted count, a total count, and optionally a confidence setting. If there are no examples, it returns 1, meaning the rate is completely uncertain on the high side. Otherwise it calculates the Wilson upper confidence bound and returns that number capped at 1.

**Call relations**: The lift calculations call this when they need the optimistic side of an acceptance rate. Like wilson_lower_bound, it uses square root math to account for uncertainty, then returns the bound to lift_lower_bound or lift_upper_bound.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `lift_lower_bound`  (lines 73–84)

```
def lift_lower_bound(cont: Contingency) -> float
```

**Purpose**: This function estimates the worst believable improvement of the candidate prompt over the old prompt. It answers, 'Even after accounting for uncertainty, how much better does the candidate seem to be?'

**Data flow**: It receives a Contingency object containing accepted and total counts for candidate-present and candidate-absent examples. If either side has no examples, it returns 0. Otherwise it compares the observed acceptance rates, subtracts an uncertainty allowance built from Wilson bounds, and returns a conservative lower bound on the lift.

**Call relations**: score_gate calls this after building the counts for the local task examples. To calculate the conservative lift, this function asks wilson_lower_bound for the candidate's cautious low rate and wilson_upper_bound for the old prompt's cautious high rate.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (score_gate); 1 external calls (sqrt).


##### `lift_upper_bound`  (lines 87–98)

```
def lift_upper_bound(cont: Contingency) -> float
```

**Purpose**: This function estimates the best believable improvement of the candidate prompt over the old prompt. It is mainly used to decide whether a candidate is clearly harmful on other task classes.

**Data flow**: It receives a Contingency object with candidate-present and candidate-absent counts. If either side has no examples, it returns 0. Otherwise it compares the observed acceptance rates, adds an uncertainty allowance, and returns an optimistic upper bound on the lift.

**Call relations**: global_non_inferior calls this during the wider safety check. The function uses wilson_upper_bound for the candidate side and wilson_lower_bound for the old-prompt side so it can ask whether the candidate could still plausibly be acceptable.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (global_non_inferior); 1 external calls (sqrt).


##### `contingency`  (lines 101–109)

```
def contingency(labels: tuple[OutcomeLabel, ...]) -> Contingency
```

**Purpose**: This function turns individual replay results into the four counts the statistical checks need. It separates examples where the candidate prompt was present from examples where it was absent, then counts successes in each group.

**Data flow**: It receives a tuple of OutcomeLabel records. It splits them into present and absent groups, counts how many examples are in each group, counts how many succeeded in each group, and returns a Contingency object containing those four numbers.

**Call relations**: Both score_gate and global_non_inferior call this before doing their statistical checks. It is the bridge between raw replay labels and the lift calculations.

*Call graph*: called by 2 (global_non_inferior, score_gate); 1 external calls (__init__).


##### `score_gate`  (lines 112–139)

```
def score_gate(labels: tuple[OutcomeLabel, ...], lower_bound: float=LIFT_LOWER_BOUND, n_floor: int=N_FLOOR) -> GateVerdict
```

**Purpose**: This function makes the local promotion decision for the task class the candidate was meant to improve. It requires enough replay examples on both sides and a conservative improvement estimate above the configured floor.

**Data flow**: It receives local replay labels, plus optional thresholds for the required lift and minimum examples per side. It converts labels into counts, computes the lower bound on improvement, then returns a GateVerdict. The verdict says whether the candidate passed, why it passed or failed, the measured lower bound, and the sample sizes.

**Call relations**: two_stage_gate calls this first. If score_gate says the local evidence is not strong enough, the full process stops there. Internally, score_gate relies on contingency to count the data and lift_lower_bound to judge whether the improvement is convincing.

*Call graph*: calls 2 internal fn (contingency, lift_lower_bound); called by 1 (two_stage_gate); 1 external calls (__init__).


##### `global_non_inferior`  (lines 142–154)

```
def global_non_inferior(labels: tuple[OutcomeLabel, ...], margin: float=GLOBAL_REGRESSION_MARGIN, n_floor: int=N_FLOOR) -> bool
```

**Purpose**: This function checks that a candidate prompt does not clearly make other task classes worse. It is intentionally forgiving when evidence is too small or noisy, and only rejects when the data confidently shows meaningful harm.

**Data flow**: It receives replay labels from the broader global task set, plus optional settings for the harm margin and minimum examples per side. It turns labels into counts. If either side has too few examples, it returns true. Otherwise it computes the optimistic upper bound on lift and returns whether that value is not below the allowed negative margin.

**Call relations**: two_stage_gate calls this only after the local task check has passed. It uses contingency to count global examples and lift_upper_bound to decide whether even the best plausible interpretation still shows regression.

*Call graph*: calls 2 internal fn (contingency, lift_upper_bound); called by 1 (two_stage_gate).


##### `two_stage_gate`  (lines 157–174)

```
def two_stage_gate(local_labels: tuple[OutcomeLabel, ...], global_labels: tuple[OutcomeLabel, ...]) -> GateVerdict
```

**Purpose**: This function gives the final yes-or-no verdict for promoting a candidate prompt. It requires both a convincing local win and no clear global regression.

**Data flow**: It receives two groups of replay labels: local labels for the target task class and global labels for other task classes. It first runs score_gate on the local labels. If that fails, it returns that failure verdict. If the local gate passes, it runs global_non_inferior. If the global check finds clear harm, it returns a failing GateVerdict with the local evidence attached. Otherwise it returns the successful local verdict.

**Call relations**: This is the top-level decision function in the file. It coordinates score_gate for improvement evidence and global_non_inferior for safety evidence, producing the verdict that outside self-improvement code can use to decide whether a prompt candidate should be promoted.

*Call graph*: calls 2 internal fn (global_non_inferior, score_gate); 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/replay.py`

`domain_logic` · `self-improvement evaluation`

This file answers a careful “what if?” question: if the agent had been given a different system prompt, would it have produced a better final answer on a past task? To make that comparison fair and safe, it does not rerun the whole task in the real world. Instead, it keeps the archived conversation, removes the old final answer, and asks the model to continue from there under the new prompt.

The important safety rule is that tools are never actually executed during replay. If the model asks for a tool call that matches one from the archive, the file feeds back the same archived result. This is like rehearsing a play with recorded sound effects instead of firing the real prop cannon. If the model asks for a tool call that was not in the archive, the replay stops because it has left the known path.

The file also strips out model “reasoning” blocks before replay. Those blocks can be tied to the model or provider that originally created them, and including them could cause rejection or leak irrelevant internal material. The main class, ReplayEvaluation, ties the pieces together: prepare the old conversation, build a small fake tool catalog from archived calls, run the model for a few rounds, feed back archived tool results when possible, and return the regenerated final text.

#### Function details

##### `_canonical_input`  (lines 40–41)

```
def _canonical_input(value: object) -> str
```

**Purpose**: This turns a tool input into a stable text key so the replay can tell when a new tool request matches an archived one. It avoids treating the same input as different just because dictionary fields appeared in a different order.

**Data flow**: It receives any input value, such as a tool argument object. It converts that value to compact JSON text with sorted keys. The returned string is used as part of a lookup key for archived tool results.

**Call relations**: When archived_tool_results builds its index, it uses this helper to label each old tool call consistently. Later, _feed_archived uses the same helper on a replayed tool call, so both sides speak the same matching language.

*Call graph*: called by 2 (_feed_archived, archived_tool_results); 1 external calls (dumps).


##### `replay_head`  (lines 44–60)

```
def replay_head(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This prepares the archived conversation for replay by removing the original final answer while keeping the useful context. It lets the model generate a fresh answer under a new system prompt instead of seeing the old answer it is supposed to replace.

**Data flow**: It receives the full archived message history. It removes trailing assistant messages that are final answers rather than tool requests, then passes each remaining message through _without_reasoning to remove provider-specific reasoning blocks. It returns the cleaned conversation prefix that replay should start from.

**Call relations**: ReplayEvaluation.replay calls this near the start of a replay. replay_head delegates message cleanup to _without_reasoning, then hands the cleaned message history back to the main replay loop.

*Call graph*: calls 1 internal fn (_without_reasoning); called by 1 (replay).


##### `_without_reasoning`  (lines 63–71)

```
def _without_reasoning(message: Message) -> Message
```

**Purpose**: This removes internal reasoning-style blocks from a message while leaving normal text, tool calls, and tool results intact. It exists because replay should be safe and portable, and those reasoning blocks may not be accepted by the model used for replay.

**Data flow**: It receives one message. If the message is plain text, it returns it unchanged. If the message is made of blocks, it filters out thinking or reasoning blocks and returns a new message with the same role and the remaining content.

**Call relations**: replay_head calls this for every message it keeps. It is a cleanup step before ReplayEvaluation.replay sends the old conversation back through the model.

*Call graph*: called by 1 (replay_head); 1 external calls (__init__).


##### `archived_tool_results`  (lines 74–96)

```
def archived_tool_results(messages: tuple[Message, ...]) -> dict[tuple[str, str], ToolResultBlock]
```

**Purpose**: This builds a lookup table from each archived tool request to the result that request originally received. That table is what lets replay answer tool calls without touching real tools.

**Data flow**: It receives the archived messages. First it records tool result blocks by their tool-use id. Then it finds tool-use blocks, matches each to its result, and stores the result under a key made from the tool name and canonicalized input. It returns a dictionary that can answer “have we seen this exact tool call before?”

**Call relations**: ReplayEvaluation.replay calls this before the replay loop begins. _feed_archived later consults the dictionary it produced whenever the model asks for tools during replay.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay).


##### `replay_tools`  (lines 99–116)

```
def replay_tools(messages: tuple[Message, ...]) -> tuple[ToolSchema, ...]
```

**Purpose**: This creates a small fake tool catalog containing only the tools that appeared in the archived conversation. It gives the model enough information to repeat old tool calls, without giving this replay code access to the live agent’s real tool registry.

**Data flow**: It receives the archived messages and scans them for tool-use blocks. It keeps each distinct tool name once, preserving first-seen order. It returns simple ToolSchema objects with permissive input shapes, meaning the model may provide ordinary object-like arguments.

**Call relations**: ReplayEvaluation.replay calls this during setup. The resulting tool list is passed into the model turn, so the model knows which archived tools it is allowed to request.

*Call graph*: called by 1 (replay); 1 external calls (__init__).


##### `_feed_archived`  (lines 119–135)

```
def _feed_archived(tool_uses: tuple[ToolUseBlock, ...], results: Mapping[tuple[str, str], ToolResultBlock]) -> Message | None
```

**Purpose**: This answers the model’s replayed tool calls using archived results. If any requested call was not seen in the archive, it signals that replay has diverged and should stop.

**Data flow**: It receives the tool calls from the current model turn and the archived-result lookup table. For each call, it searches for a matching old result using the tool name and canonicalized input. If all calls match, it creates a new user message containing tool result blocks with the current call ids but the old result contents. If one call has no match, it returns None.

**Call relations**: ReplayEvaluation.replay calls this whenever the model asks for tools. It uses _canonical_input to match calls reliably, and it creates the tool-result message that gets appended to the replay conversation for the next model turn.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay); 2 external calls (__init__, __init__).


##### `ReplayEvaluation.replay`  (lines 148–169)

```
async def replay(self, archived: tuple[Message, ...], system_prompt: str) -> ReplayResult
```

**Purpose**: This is the main replay procedure. It runs one archived task against one new system prompt and returns the final text that the model produced, while never executing real tools.

**Data flow**: It receives an archived message history and a candidate system prompt. It builds the archived tool-result lookup, creates the replay-only tool catalog, and strips the old final answer from the conversation. Then it repeatedly asks the model for the next assistant message. If the model gives a final answer, that text is returned. If the model asks for tools, the method feeds back matching archived results and continues. If the model asks for an unknown tool call or the round limit is reached, it returns the best text seen so far.

**Call relations**: This method coordinates all helpers in the file. It calls archived_tool_results, replay_tools, and replay_head during setup, then uses _feed_archived inside the replay loop. It is the point other self-improvement code would call when it wants a counterfactual answer for a prompt candidate.

*Call graph*: calls 4 internal fn (_feed_archived, archived_tool_results, replay_head, replay_tools); 1 external calls (__init__).


### Scheduled improvement loop
Runs the recurring self-improvement workflow and opens governed proposals only for candidates that repeatedly pass evaluation.

### `extensions/self_improvement/ufo_ext_self_improvement/cron.py`

`orchestration` · `scheduled background tick`

This file is the heartbeat of the self-improvement extension. On each scheduled tick, it reviews past agent runs, grouped by agent, and decides whether there is a prompt change worth proposing. It does not directly rewrite an agent. That is important: without this file, prompt improvements would not be discovered and promoted automatically; with it, they still must pass checks and then go through the normal approval path.

The flow is deliberately cautious. For each agent, the code checks whether there is already a stored candidate prompt for the agent’s current prompt version. Think of the stored candidate like a ticket on a clipboard: if the same prompt version is still being tested, the file continues that ticket instead of starting over. If the ticket was already promoted or rejected, it stays quiet until the agent’s approved prompt changes.

If there is no active candidate, the file asks a prompt proposer to suggest one based on a task class found in the agent’s trajectories. It stores the candidate, including a held-out test set. On later ticks, it evaluates the candidate against held-out examples from the same task and against examples from other tasks, so the new prompt must improve without breaking other behavior. A candidate must pass for multiple consecutive ticks before this file opens an `AgentChange`, which is a governed proposal for someone else to approve.

#### Function details

##### `ImproveCron.run`  (lines 50–52)

```
async def run(self) -> None
```

**Purpose**: This is the top-level scheduled action for the self-improvement loop. It gathers all available trajectories, groups them by agent, and advances each agent’s improvement candidate independently.

**Data flow**: It reads trajectories from the extension context. It turns one mixed collection of agent runs into separate bundles per agent, then passes each bundle onward. It does not return a value; its effect is to start or continue candidate testing for every agent seen in the data.

**Call relations**: This function begins the file’s main story. It uses `_by_agent` to sort trajectories into per-agent groups, then calls `ImproveCron._advance` once for each group so each agent gets its own improvement decision.

*Call graph*: calls 2 internal fn (_advance, _by_agent).


##### `ImproveCron._advance`  (lines 54–60)

```
async def _advance(self, agent_id: UUID, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: This advances the self-improvement process for one specific agent. It either finds or opens a candidate prompt, then sends that candidate through the evaluation gate.

**Data flow**: It receives an agent ID and that agent’s trajectories. From the first trajectory it reads the current prompt digest, which is a fingerprint of the prompt version being tested. It asks for an active candidate or opens a new one, and if one exists, it passes the candidate into the gate step. It changes persistent state only through the helper steps it calls.

**Call relations**: `ImproveCron.run` calls this after grouping trajectories. `_advance` coordinates the two main phases for one agent: `ImproveCron._active_or_open` decides what candidate, if any, is in play, and `ImproveCron._gate` tests that candidate and may turn it into a governed proposal.

*Call graph*: calls 2 internal fn (_active_or_open, _gate); called by 1 (run).


##### `ImproveCron._active_or_open`  (lines 62–83)

```
async def _active_or_open(self, key: str, from_digest: str, trajectories: tuple[Trajectory, ...]) -> CandidateState | None
```

**Purpose**: This finds the current candidate prompt for an agent, or creates one if it is safe and useful to do so. It prevents the system from repeatedly proposing the same resolved candidate for the same prompt version.

**Data flow**: It receives a storage key, the current prompt digest, and the agent’s trajectories. It first reads the scoped store to see whether a candidate already exists. If the stored candidate belongs to the same prompt version and is still evaluating, it returns that candidate; if it was already promoted or rejected, it returns nothing. If there is no usable candidate, it looks for task classes in the trajectories, asks the proposer for a new prompt, stores the new candidate with its held-out conversation IDs, and returns it.

**Call relations**: `ImproveCron._advance` calls this before any evaluation happens. It relies on `task_classes` to find meaningful groups of examples and on the prompt proposer to create a possible improved prompt. Its result decides whether `_advance` continues into `ImproveCron._gate` or stops for this tick.

*Call graph*: called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._gate`  (lines 85–117)

```
async def _gate(self, agent_id: UUID, key: str, from_digest: str, candidate: CandidateState, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: This is the safety checkpoint for a candidate prompt. It evaluates whether the candidate passes its tests, counts consecutive successful ticks, and only then opens a governed proposal.

**Data flow**: It receives the agent ID, storage key, prompt digest, candidate state, and trajectories. It builds two test sets: the candidate’s own held-out examples and held-out examples from other task classes. It asks the evaluator to compare the candidate prompt with the current prompt. If the candidate fails, it saves the candidate as rejected. If it passes but has not passed enough times yet, it saves the increased pass count. If it has passed enough consecutive ticks, it asks the extension context to propose an `AgentChange` and saves the candidate as promoted with the proposal ID.

**Call relations**: `ImproveCron._advance` calls this after a candidate has been found or opened. `_gate` calls `_held_out` to reconstruct the candidate’s specific test examples, calls `task_classes` to gather cross-task tests, and calls `ImproveCron._save` whenever it needs to persist the new candidate status. It is the only place in this file that can open a governed change proposal.

*Call graph*: calls 2 internal fn (_save, _held_out); called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._save`  (lines 119–131)

```
async def _save(self, key: str, candidate: CandidateState, *, status: CandidateStatus, gate_passes: int, proposal_id: str | None=None) -> None
```

**Purpose**: This writes an updated candidate state back to the extension’s scoped store. It is used after a candidate passes, fails, or is promoted.

**Data flow**: It receives the storage key, the existing candidate, and the new status information. It creates a copied candidate with updated status, pass count, and optional proposal ID, then stores that copied version as JSON-friendly data. It does not return anything; the lasting result is the updated record in persistent storage.

**Call relations**: `ImproveCron._gate` calls this whenever the evaluation outcome changes the candidate’s state. This keeps all candidate-state writes in one small helper, so the gate step can focus on the decision rather than the storage details.

*Call graph*: called by 1 (_gate); 1 external calls (model_copy).


##### `_by_agent`  (lines 134–138)

```
def _by_agent(trajectories: tuple[Trajectory, ...]) -> Mapping[UUID, tuple[Trajectory, ...]]
```

**Purpose**: This separates a mixed list of trajectories into one group per agent. It gives the cron loop a clean way to treat each agent independently.

**Data flow**: It receives a tuple of trajectories. It reads each trajectory’s agent ID, collects trajectories with the same agent ID together, and returns a mapping from each agent ID to that agent’s tuple of trajectories. It does not change the trajectories themselves.

**Call relations**: `ImproveCron.run` calls this at the start of the tick. The grouped result is what lets `ImproveCron._advance` work on one agent at a time instead of mixing evidence from different agents.

*Call graph*: called by 1 (run).


##### `_held_out`  (lines 141–153)

```
def _held_out(trajectories: tuple[Trajectory, ...], held_out: tuple[str, ...]) -> tuple[TaskExample, ...]
```

**Purpose**: This rebuilds the candidate’s held-out test examples from the latest trajectories. It only includes examples that are recognized as bad trajectories, because those are the examples used to judge whether the new prompt fixes known problems.

**Data flow**: It receives all trajectories for an agent and a tuple of conversation ID strings that were saved with the candidate. It looks up each requested conversation, skips any that are missing, and runs `bad_trajectory` on the found trajectory. When a trajectory is flagged as bad, it takes the associated task example and adds it to the output tuple.

**Call relations**: `ImproveCron._gate` calls this when preparing the candidate’s own held-out evaluation set. `_held_out` relies on `bad_trajectory` to translate raw trajectory history into the task examples that the evaluator can score.

*Call graph*: called by 1 (_gate); 1 external calls (bad_trajectory).
