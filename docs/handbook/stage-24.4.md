# Scheduled Task Cron Parsing Helper  `stage-24.4`

This stage is a small behind-the-scenes helper for the scheduled-tasks extension. It is not the code that runs the task itself. Instead, it answers two practical questions: “Is this schedule written correctly?” and “When should it run next?”

The single file, `cron.py`, understands cron expressions. A cron expression is a compact five-part schedule, commonly used to say things like “run every day at 2:00” or “run every Monday morning.” The helper checks that the schedule matches the expected five fields, so invalid schedules can be caught before they cause confusion.

It also calculates the next run time from a valid schedule. This lets the rest of the scheduled-task system focus on storing tasks and starting them at the right moment. In everyday terms, this file is the calendar reader: it does not perform the job, but it reads the timetable and points to the next appointment.

## Files in this stage

### Scheduled Task Cron Parsing Helper
### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/cron.py`

`domain_logic` · `scheduled task creation and runner polling`

Scheduled tasks need a way to say “run every day at 9” or “run every five minutes.” That compact timing language is called cron. This file keeps that cron knowledge inside the scheduled-tasks extension, so the main task store only has to care about concrete dates like “next run at 2026-08-03 09:00.”

It does two small but important jobs. First, it validates a cron schedule before the rest of the system trusts it. It requires exactly five fields, such as minute, hour, day of month, month, and day of week. This prevents accidentally accepting a schedule written for a different cron style. Then it asks the external `croniter` library to confirm the expression is meaningful.

Second, it computes the next firing time after a given moment. The important detail is that it moves strictly forward past the supplied time. If a runner wakes up late, this avoids creating a burst of every missed run. Instead, missed windows collapse into one catch-up opportunity, like checking the next bus after you arrive rather than trying to board all the buses you missed.

#### Function details

##### `validate_cron`  (lines 14–19)

```
def validate_cron(schedule: str) -> str
```

**Purpose**: Checks whether a schedule string is a valid five-field cron expression. Code would use this before saving or accepting a scheduled task, so bad timing rules are rejected early with a clear error.

**Data flow**: A schedule string comes in. The function first splits it into space-separated parts and makes sure there are exactly five. Then it asks `croniter`, a library that understands cron expressions, whether the schedule is valid. If anything is wrong, it raises a `ValueError`; if everything is right, it returns the same schedule string unchanged.

**Call relations**: This is the gatekeeper before cron text is trusted by the rest of the scheduled-task system. It delegates the detailed cron syntax check to `croniter.croniter.is_valid`, while keeping this extension’s own rule that only five-field cron expressions are allowed.

*Call graph*: 1 external calls (is_valid).


##### `next_fire`  (lines 22–23)

```
def next_fire(schedule: str, after: datetime) -> datetime
```

**Purpose**: Calculates the next datetime when a cron schedule should run after a given moment. This lets the scheduler turn a repeating rule into the concrete `next_run_at` time that the task store can sort and poll.

**Data flow**: A cron schedule and a starting datetime come in. The function gives both to `croniter`, which builds a cron calculator positioned at that starting time. It then asks for the next matching datetime and returns that datetime to the caller.

**Call relations**: This function is used when scheduled-task code needs to advance a task from “it just ran” or “we are checking now” to its next planned run. It hands the cron math to `croniter.croniter`, so this file stays small while still enforcing the project’s behavior of moving forward to the next fire time.

*Call graph*: 1 external calls (croniter).
