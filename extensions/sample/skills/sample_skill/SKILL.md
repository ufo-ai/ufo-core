---
name: sample_skill
description: The conformance sample's probe skill — proves a manifest-contributed skill parses into the registry, renders in the skill index, and mounts into the sandbox with its bundled script.
---
# Sample Skill

This skill exists only to exercise the `skills` Manifest seam end to end.

Run its bundled script in the sandbox to emit the probe marker:

```bash
python .skills/sample_skill/probe.py
```
