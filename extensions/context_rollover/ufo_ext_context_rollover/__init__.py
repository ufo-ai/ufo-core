"""The rollover context boundary: reset the window at the line and keep the outgoing window in the
conversation's sandbox history file. Registers the `rollover` strategy at the `context_boundaries`
Manifest point and ships the two tools that boundary offers, `new_context` and `search_history`."""
