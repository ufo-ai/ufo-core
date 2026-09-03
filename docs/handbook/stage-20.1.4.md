# SDK package root and cross-cutting operational exports  `stage-20.1.4`

This stage is shared support for the public SDK, not part of the main work loop itself. It acts like the front desk of a large building: extension code can ask here for common tools without needing to know the internal room numbers where those tools are built.

The package marker file, __init__.py, simply tells Python that ufo.sdk is an importable package. It adds no behavior, but it makes the rest of the SDK doorway usable. accounting.py provides one public place to import accounting and spending value types, while leaving the real calculations to deeper billing code. balance.py does the same for balance checks and balance-related helpers used by extensions. flags.py exposes feature flags, which are switches used to turn behavior on or off, by re-exporting the internal flag helper and constants. o11y.py, short for observability, gives extensions safe access to logging, warnings, metrics, and turn timing so their activity can be seen and measured consistently.

## Files in this stage

### SDK package anchor
The package initializer establishes the SDK namespace before any public helper modules are imported.

### `core/src/ufo/sdk/__init__.py`

`other` · `import time`

In Python, a folder often needs an `__init__.py` file to be treated as a package: a named area of code that other files can import from. This file plays that role for `core/src/ufo/sdk`. Think of it like a label on a drawer: the drawer may contain useful tools, and the label lets the rest of the system find them by name.

Right now the file is empty, so it does not run setup code, expose shortcuts, or define any classes or functions. Its value is structural rather than active. Without it, depending on the Python version and packaging setup, imports that expect `ufo.sdk` to be a regular package might fail or behave differently. Keeping the file also gives the project a clear place to add package-level documentation or import conveniences later if needed.


### Billing and feature exports
These modules provide stable SDK-level import paths for accounting values, balance helpers, and feature flag constants.

### `core/src/ufo/sdk/accounting.py`

`data_model` · `cross-cutting`

This module is like a labeled shelf at the front of a warehouse. The real accounting code lives elsewhere, but outside users should not need to know the warehouse layout to find it. By importing from `ufo.sdk.accounting`, callers can get the objects used to describe workspace spending, member spending, usage exports, pricing units, and metered workspaces.

The file exists because `ufo.sdk` is arranged as a public-facing package made of small named modules. Its empty package initializer means the stable public surface is provided by files like this one. That matters because external code can depend on a simple import path without reaching into internal runtime or harness modules, which may be more likely to change.

The key idea is re-exporting: this file imports names from their internal homes and exposes them again under the SDK path. For example, `SpendReport` and `MemberSpendReport` come from the billing accounting layer, while `MICRO_USD_PER_USD` comes from pricing models. A surface can use these objects to show the same spending rollup that the `ufoctl spend` command prints. If this file were removed, the accounting data might still exist internally, but SDK users would lose this convenient and intentional public doorway to it.


### `core/src/ufo/sdk/balance.py`

`other` · `cross-cutting import-time SDK access`

This file does not implement billing rules itself. Instead, it re-exports selected names from the internal billing balance module, `ufo.runtime.billing.balance`, and presents them as part of the public SDK, which is the safer, intended interface for extensions.

In plain terms, it works like a reception desk. The real work happens in the back office, but visitors are told to come to this desk so they do not need to wander through private hallways. A billing extension can use this module to read a workspace's prepaid balance, add credit after a payment succeeds, inspect recent purchases, or configure automatic top-ups. The extension decides when those actions should happen, while the core billing module keeps control of the actual rules.

This separation matters because internal file paths and implementation details can change over time. By keeping a stable public import path here, extension authors get a predictable API. Without this file, outside code might import directly from internal runtime modules, making it more likely to break when the project is reorganized.


### `core/src/ufo/sdk/flags.py`

`other` · `cross-cutting`

This file is a small public doorway into the project’s feature-flag system. A feature flag is a switch that lets the backend turn a behavior on or off without changing the caller’s code. Instead of asking SDK users to import from the deeper internal module `ufo.flags`, this file exposes the pieces they are meant to use through `ufo.sdk.flags`.

It re-exports three names. `flag_enabled` is the helper used to ask whether a named feature is currently turned on. `SERVED_TRUE` and `SERVED_FALSE` are the two backend spellings for an enabled or disabled flag value. In plain terms, this file is like a labeled service counter: the real supplies are stored elsewhere, but callers come here because this is the supported public counter.

Nothing is calculated here, and no state is stored here. Its value is stability and clarity. If outside code imports flags through this SDK path, the project can reorganize its internal modules later without forcing those outside callers to change their imports.


### Observability exports
The observability module exposes logging, warnings, metrics, and turn timing utilities for extension code.

### `core/src/ufo/sdk/o11y.py`

`io_transport` · `cross-cutting`

This file is a thin public wrapper around the project’s observability tools. Observability means the signals a running system gives you so you can understand what happened, such as logs, warnings, metrics, and timing profiles. Extensions import these names from the SDK instead of reaching directly into the internal harness package.

The important idea is control. Metrics are counted using names that the core system already knows about. An extension can emit one of those approved metric names, but it cannot invent new ones freely. That matters because metrics are usually collected across many machines or runs; if every extension could create arbitrary metric names, the monitoring data would become messy and hard to reason about. This file is like a front desk: extensions can ask for the approved services, but the building still decides what services exist.

There is no extra behavior here. The file simply re-exports four tools from `ufo.harness.o11y`: `log`, `warn`, `emit_metric`, and `turn_profile`. That keeps the SDK surface stable and friendly while allowing the internal implementation to live elsewhere.
