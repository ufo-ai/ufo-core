"""The compaction context boundary: spend one model call over the head of a full window, verify
the summary against the anchors the head held, and install it in front of a verbatim tail.
Registers the `compact` strategy at the `context_boundaries` Manifest point."""
