from ufo.sdk.manifest import (
    CONVERSATION_AUTOMATION_DESCRIPTION_MAX_CHARS,
    CONVERSATION_AUTOMATION_SCHEDULE_MAX_CHARS,
    CONVERSATION_AUTOMATIONS_MAX,
    AutomationsSlotPayload,
    ConversationAutomation,
    ConversationSlotContext,
    ConversationSlotProvider,
)
from ufo_ext_scheduled_tasks.schedules import ScheduledTask, ScheduleStore

LATEST_STATUS_MAX_CHARS = 40
LATEST_RESPONSE_MAX_CHARS = 400


def _scheduler(ctx: ConversationSlotContext) -> ScheduleStore:
    if ctx.ext is None:
        raise RuntimeError("automations slot needs the scheduled-tasks ExtensionContext")
    return ScheduleStore(ctx.ext)


async def _conversation(ctx: ConversationSlotContext) -> tuple[ScheduledTask, ...]:
    names = tuple(item.name for item in ctx.visible_items)
    return await _scheduler(ctx).list(
        conversation_id=ctx.conversation_id,
        names=names,
        limit=CONVERSATION_AUTOMATIONS_MAX + 1,
    )


async def _read(ctx: ConversationSlotContext) -> AutomationsSlotPayload:
    scheduler = _scheduler(ctx)
    rows = await _conversation(ctx)
    expected = {item.name: item for item in ctx.visible_items}
    authorized = tuple(
        task
        for task in rows
        if (item := expected.get(task.name)) is not None and item.generation == task.id
    )
    inspected = authorized[:CONVERSATION_AUTOMATIONS_MAX]
    inspections = {} if not inspected else await scheduler.inspect_many(inspected)
    automations: list[ConversationAutomation] = []
    truncated = len(authorized) > CONVERSATION_AUTOMATIONS_MAX
    for task in authorized[:CONVERSATION_AUTOMATIONS_MAX]:
        inspection = inspections.get(task.id)
        if inspection is None:
            truncated = True
            continue
        content_visible = expected[task.name].content_visible
        description = task.description[:CONVERSATION_AUTOMATION_DESCRIPTION_MAX_CHARS]
        schedule = task.schedule[:CONVERSATION_AUTOMATION_SCHEDULE_MAX_CHARS]
        latest_status = (
            None
            if inspection.last_turn_status is None
            else inspection.last_turn_status[:LATEST_STATUS_MAX_CHARS]
        )
        latest_response = (
            None
            if inspection.last_response is None or not content_visible
            else inspection.last_response[:LATEST_RESPONSE_MAX_CHARS]
        )
        truncated = truncated or (
            (content_visible and len(task.description) > len(description))
            or len(task.schedule) > len(schedule)
            or (
                inspection.last_turn_status is not None
                and len(inspection.last_turn_status) > LATEST_STATUS_MAX_CHARS
            )
            or (
                content_visible
                and inspection.last_response is not None
                and len(inspection.last_response) > LATEST_RESPONSE_MAX_CHARS
            )
        )
        automations.append(
            ConversationAutomation(
                name=task.name,
                description=description if content_visible else None,
                schedule=schedule,
                paused=task.paused,
                next_run_at=inspection.next_run_at,
                last_run_at=inspection.last_run_at,
                latest_status=latest_status,
                latest_response=latest_response,
                created_at=task.created_at,
                updated_at=task.updated_at,
                authorization_generation=task.id,
            )
        )
    return AutomationsSlotPayload(automations=tuple(automations), truncated=truncated)


async def _summarize(ctx: ConversationSlotContext) -> int | None:
    count = min(len(ctx.visible_items), CONVERSATION_AUTOMATIONS_MAX)
    return count or None


AUTOMATIONS_SLOT = ConversationSlotProvider(
    id="automations",
    label="Automations",
    icon="calendar",
    content=AutomationsSlotPayload,
    summarize=_summarize,
    read=_read,
)
