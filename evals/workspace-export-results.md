# Workspace export evaluation

Base: `e79f797619391da8a9616ea0744a22efb6ecfd1a`. Model: `z-ai/glm-5.3-flash`, medium reasoning.
Four authored cases, two repeats per arm. No excluded samples.

| Description | Passed | Run IDs |
|---|---:|---|
| Full | 8/8 | `6db66bcb-9e7b-40a1-9a33-8418163d6f4b`, `adb24a2f-86eb-4882-8521-edbbcb460ebb` |
| Short | 8/8 | `4bc4f06c-afff-454e-8310-4a64baea80d6`, `f1f56b9b-dbdd-47ae-990e-b601975fb433` |
| Empty | 8/8 | `2864f7de-3c0e-49a5-bc39-e1127f0b5fdf`, `7e7cde38-7652-4a24-81ec-f9534a67eebb` |

The cases cover a direct export request, a move away from ufo, private delivery refusal in a shared conversation, and a roster question that must not export.

No case changed across arms. The action ships with an empty description; its name, schema, and enforced access rules remain.

Run `uv run python -m evals.ablate evals/workspace-export-experiment.toml` with the configured provider credentials and built egress and client binaries. Raw records and the report are retained under `eval-reports/experiments/workspace-export-routing/`.
