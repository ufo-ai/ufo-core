# Pack selection and extension loading  `stage-3`

This stage happens during startup, before the product begins serving users. Its job is to decide what kind of product is being assembled, then safely load the add-on packages that make that product work.

First, the pack recipe acts like a menu choice. It says which tools, prompts, skills, apps, and integrations should be turned on for a local assistant, hosted assistant, billing test setup, evaluation run, or small sample product. This gives the system a clear shape before anything else is registered.

Next, the extension loader finds the installed extensions. An extension is an add-on package, and its manifest is a registration card listing what it offers: tools, workspace surfaces, object types, model providers, search or browser backends, jobs, skills, agents, hooks, and credential slots. The loader checks these packages against a lockfile, which is an approved list that helps ensure the loaded code is expected and unchanged.

Together, the pack chooses the recipe, the lockfile guards the door, and the manifests register the parts the running system can use.

## Sub-stages

- [Pack recipes and product composition](stage-3.1.md) `stage-3.1` — 7 files
- [Extension manifests and lockfile enforcement](stage-3.2.md) `stage-3.2` — 34 files

## 📊 State Registers Touched

- `reg-effective-config` — The merged deployment settings that tell the service how to start, where storage is, and which runtime options are enabled.
- `reg-pack-extension-registry` — The approved set of installed packs and extensions, including what tools, jobs, agents, hooks, providers, and surfaces they add.
- `reg-extension-store` — The per-workspace saved data that extensions use to remember their own settings and state.
- `reg-tool-catalog` — The shared menu of tools the agent may call, including built-in tools, extension tools, and guarded bridge tools.
- `reg-prompt-skill-environment` — The saved instructions, skills, environment documents, and fingerprints that shape what an agent sees for a turn.
