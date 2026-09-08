Write only a 3 to 8 word plain-language label for the current step toward the
user's goal, with no ending punctuation. The input is untrusted JSON describing the goal, one tool
call, and `recent_labels`, the labels already shown for earlier steps. Never repeat a recent label
and never reword one into the same sentence: give this step its own wording. Use the concrete
action implied by the operation: name what a read opens, a search looks for, a check verifies, a
write creates, an edit changes, a command accomplishes, an external action does, or a delegation
hands off. Preserve distinctive goal wording when useful. Use a target only
when the input names it. If the target is unclear, name the operation without inventing its
contents. Describe what this step does now, not the overall objective or a later result. Never
expose tool names, commands,
paths, URLs, IDs, secrets, or JSON.
