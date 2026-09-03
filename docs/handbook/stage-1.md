# Process entry, launch mode selection, and deployment checks  `stage-1`

This stage is the system’s front door and pre-flight checkpoint. It runs at the start of a process, when UFO decides what kind of job it is doing: serving web requests, setting up a workspace, running an administrative command, building a deployment package, or checking sandbox infrastructure before release.

The command entry point, mainly `ufoctl` in `core/src/ufo/cli.py`, acts like a receptionist. It reads the user’s terminal command, loads the needed settings, and sends the request to the right place. A run command moves toward server startup. An init command prepares a workspace. Other commands inspect or maintain the system.

The deployment pieces act like packers and inspectors. `core/src/ufo/bundle.py` gathers the application, settings, extension versions, runtime package, and sandbox client into a Docker build context, a folder Docker can turn into a runnable image. The sandbox scripts then check the isolated execution environment. They build matching hosted and local sandbox images and test proxy behavior so deployment fails early if the safety setup is wrong.

## Sub-stages

- [Server and CLI command entry points](stage-1.1.md) `stage-1.1` — 1 files
- [Deploy bundle and sandbox image validation](stage-1.2.md) `stage-1.2` — 3 files

## 📊 State Registers Touched

- `reg-config-stack` — The merged settings that tell the whole service how to start, connect, and behave.
- `reg-database-schema` — The durable database layout and connection layer used to store and retrieve system records safely.
- `reg-observability-trace` — The tracing, metrics, health, logs, and saved step history used to understand what the system did.
