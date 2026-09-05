# Durable queues, waits, and stop requests  `stage-7.2`

This stage is behind-the-scenes support for keeping long-running conversation work under control, even when people cancel things or the system restarts. Its wider job is to make choices about queues, pauses, wake-ups, repeated jobs, and stop requests durable, meaning they are saved so they are not lost after a crash.

The file in this stage, `stop.py`, focuses on one important case: stopping a running turn in a conversation. A “turn” is one unit of work in the conversation, like one reply being produced. Before stopping it, the code checks that the turn really belongs to the conversation asking for the stop. That prevents the wrong work from being cancelled. It then cancels the turn in a safe way, so the rest of the system sees a clean ending rather than a half-broken task. It also notifies any live listeners, such as clients waiting for updates, that the turn has ended. If requested, it can then advance the conversation into a follow-up turn, so cancellation can lead smoothly into the next step.

## Files in this stage

### Durable queues, waits, and stop requests
### `core/src/ufo/runtime/surfaces/stop.py`

`orchestration` · `request handling`

This file is the “stop button” for an active conversation turn. A turn is one running unit of work in a conversation. When a member presses stop, the system must be careful: it should not cancel someone else’s turn, it should not damage a turn that already finished on its own, and it should wake up any live views that are waiting for updates.

The main class, `MemberStop`, brings together three outside helpers. It uses the database to confirm ownership, a cancellation primitive to durably mark the turn as cancelled, and a hub to publish events to anything watching the turn. Think of the hub like a noticeboard: once the cancelled result is posted there, every open “tail” following that turn can stop waiting immediately.

The flow is deliberately ordered. First it verifies that the requested turn belongs to the requested workspace and conversation. Then it asks the shared cancellation code to cancel the turn. If the turn was already finished, it returns a result saying nothing was ended. If cancellation did happen, it asks admission logic whether there is already a follow-up message that should start a new turn. If such a new turn is founded, the hub is told which incoming message it absorbed before the old turn’s terminal cancellation is published. That order matters so screens can move cleanly from the stopped turn to the new one without waiting forever.

#### Function details

##### `MemberStop.stop`  (lines 38–61)

```
async def stop(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID) -> Stopped
```

**Purpose**: Stops one running turn on behalf of a member. It protects against stopping the wrong conversation’s turn, avoids changing turns that are already finished, and notifies live listeners when a cancellation really happens.

**Data flow**: It receives a workspace ID, conversation ID, and turn ID. It first reads the database inside a workspace transaction to find which conversation owns that turn. If the owner does not match the requested conversation, it raises an error instead of cancelling. If ownership is valid, it calls the shared cancellation routine. When that routine says there was nothing to cancel, it returns a `Stopped` result with `ended` set to false. When cancellation succeeds, it may start a follow-up turn through admission, publishes any absorbed arrival for that new turn, publishes the cancelled terminal frame for the stopped turn, and returns a `Stopped` result showing that the turn ended and naming the new founded turn if one exists.

**Call relations**: This method is the end-to-end stop workflow. It opens its database check with `workspace_tx` and builds the ownership query with `sqlalchemy.select`. It then hands the actual durable cancellation to `cancel_one_turn`, rather than inventing a separate cancellation path. If a new turn is created, it publishes an `Absorbed` hub message for that new turn, and finally publishes a `Terminal` hub message for the cancelled turn. Its returned `Stopped` object is the compact answer the surface layer can give back to the member interface.

*Call graph*: 6 external calls (__init__, __init__, __init__, select, workspace_tx, cancel_one_turn).
