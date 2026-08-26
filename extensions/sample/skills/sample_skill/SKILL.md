---
name: sample_skill
description: Load when proving a manifest-contributed skill reaches the registry, index, and sandbox with its bundled script.
---
# Sample Skill

This skill exists only to exercise the `skills` Manifest seam end to end.

Run its bundled script in the sandbox to emit the probe marker:

```bash
python "$UFO_HOME/skills/sample_skill/probe.py"
```
