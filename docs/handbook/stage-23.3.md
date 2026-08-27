# Public SDK Identity, Credentials, Billing, and Access Facades  `stage-23.3`

This stage is shared support for extension writers. It is like a row of labeled service windows at the front of a building: extensions use these public SDK modules instead of walking through private internal hallways that may change. The files mostly do not add new behavior. They re-export, meaning they make selected internal tools available again under stable public names.

The billing side is covered by accounting.py, which exposes spend and accounting data types, and balance.py, which exposes balance tools. seats.py does the same for seat and membership limits. The identity and access side is split by purpose: authproxy.py exposes pieces for supplying credentials to feed-sync connectors, credentials.py exposes approved credential classes, errors, and helpers, and grants.py exposes connection and grant audit helpers. bearer.py gives extensions a safe way to verify login bearer tokens, which are “show this to prove who you are” strings, without revealing signing secrets. subjects.py exposes standard workspace visibility subjects, such as “everyone in this workspace” or “this member.” surface_token.py exposes helpers for creating and checking surface tokens.

## Files in this stage

### Billing and entitlements
These facades expose workspace spend, balance, and seat-related SDK imports without revealing internal billing or entitlement paths.

### `core/src/ufo/sdk/accounting.py`

`data_model` · `cross-cutting`

This is a small “front desk” file for accounting-related SDK objects. The real accounting code lives deeper in `ufo.billing.accounting`, but callers should not need to know that internal location. Instead, they can import names such as `SpendReport`, `AgentSpendReport`, or `metered_workspaces` from `ufo.sdk.accounting`.

That matters because SDK modules are part of the public promise of the project. Internal folders can be reorganized later, but code written by users should keep working if it imports from the SDK surface. This file is like a reception counter: it does not manufacture the reports, but it points visitors to the right official forms.

The exported objects represent the same spending summaries shown by tools such as `ufoctl spend` and by surface context data like `SurfaceContext.spend_rollup`. They include report shapes for total spend, spend by member, spend by agent, dimension totals, usage exports, and a money conversion constant for micro-US dollars. There is no runtime behavior here beyond importing and exposing those names.


### `core/src/ufo/sdk/balance.py`

`other` · `cross-cutting: active when extensions or other public callers import billing balance features`

This file does not create new billing rules itself. Instead, it re-exports selected names from `ufo.billing.balance`, meaning it takes core billing objects and functions and makes them available through a cleaner public path. This matters because outside code, such as a billing extension, needs a stable way to read a workspace’s prepaid balance, add credit after a payment settles, inspect recent purchases, and configure automatic top-ups. Without this file, extension authors would have to import directly from the internal billing module, which would make the system harder to reorganize safely later.

Think of it like a reception desk in a large office. The real work happens in the billing department, but visitors are told to go through the front desk so the building can change its internal layout without confusing everyone. Here, `ufo.sdk.balance` is that front desk.

The file exposes data types such as `Balance`, `Headroom`, `Purchase`, and `AutoTopup`, along with actions such as `read_balance`, `credit`, `set_auto_topup`, and `mark_topup_verified`. It also exposes a billing screen fragment used by the user interface. The repeated `as same_name` style makes the public exported names explicit, which helps tools and readers see exactly what belongs to this SDK surface.


### `core/src/ufo/sdk/seats.py`

`other` · `import time / cross-cutting public API`

This module is like a signposted front door for seat-reporting code. The real rules and logic live in `ufo.seats`, but outside code should not have to know that internal layout. Instead, it can import from `ufo.sdk.seats`, which is part of the public software development kit, or SDK — the supported interface for people writing extensions.

The file re-exports the main seat data type, `SeatEntry`, the collection type, `Seats`, and helper functions for common questions such as finding a member by email, checking whether a member is an admin, listing a member’s workspaces, and getting a workspace domain.

This matters because it separates “what extension authors are allowed to use” from “where the core project happens to store its implementation today.” If the internal package structure changes later, this file can keep the public import path steady. Without it, extensions might import directly from internal modules and become fragile when the project is reorganized.

There are no functions defined here. The file is active when Python imports it, and its only job is to make selected names available under the `ufo.sdk.seats` namespace.


### Credentialed access
These modules provide stable SDK entry points for connector authentication, credential helpers, and grant auditing.

### `core/src/ufo/sdk/authproxy.py`

`data_model` · `cross-cutting`

This file is a small public doorway into the system’s authentication-proxy support. An authentication proxy is the part of an extension that can turn a source’s account reference into a real credential, such as a token or secret, that a connector can use to talk to an outside provider.

The file does not create new behavior. Instead, it re-exports a few important names from deeper modules: `AuthProxy`, the interface an extension implements; `Credential`, the credential shape connectors receive; `AuthProxySpec`, the manifest entry that declares the backend; and `DIRECT_ACCOUNT`, a marker used when the member supplied credentials directly rather than connecting through a broker account.

Its value is stability and clarity. Extension code can import from `ufo.sdk.authproxy` instead of depending on internal paths like `ufo.access.connectors`. That is like giving plugin authors a clearly labeled service counter, rather than asking them to walk into the stockroom. If the internal layout changes later, this public file can keep the outside-facing import path the same.


### `core/src/ufo/sdk/credentials.py`

`other` · `cross-cutting`

This file does not create new credential behavior itself. Instead, it acts like a clearly marked service window: extension authors can import credential-related pieces from `ufo.sdk.credentials` without needing to know where the project keeps the deeper implementation.

The real logic lives in `ufo.access.credentials`. This SDK file re-exports only the parts that are meant to be touched from outside that internal layer, such as credential stores, credential-related error types, and helpers for opening an installation or identifying deployment/workspace credential areas.

That separation matters because credentials are sensitive. If every extension imported directly from the internal credential system, the project would have a harder time keeping a stable public interface. By routing public use through this file, the project can change its private internals later while keeping the SDK-facing names steady.

In everyday terms, this file is like a reception desk. It does not manufacture keys itself, but it tells approved visitors which key-related services they may ask for and gives them the official names to use.


### `core/src/ufo/sdk/grants.py`

`io_transport` · `cross-cutting`

This module is like a clearly labeled front desk for a set of grant and connection audit tools. The real implementation lives in `ufo.access.grants`, but outside code should not need to know that internal location. Instead, extensions can import public names from `ufo.sdk.grants`, which is easier to document and safer to keep stable over time.

The file exists because this project keeps `ufo.sdk` as a set of thin, named modules rather than putting code in `__init__.py` files. That means each public area of the SDK needs its own small module. Here, the public area is about seeing or reporting which connections were allowed, denied, or recorded, and summarizing connector grants.

All entries are direct re-exports. For example, `ConnectionSummary`, `GrantSummary`, and `MainAgentConnection` are made available here, as are helper functions such as `connection_summaries`, `grant_summaries`, and `main_agent_connections`. If this file were missing, extension code would either break when importing these SDK names or would have to reach into the more internal `ufo.access.grants` module, making the public boundary less clear.


### Identity and token helpers
These facades expose public token-checking, workspace subject, and surface-token helpers for extensions.

### `core/src/ufo/sdk/bearer.py`

`util` · `request handling`

This file is a small public doorway into the project’s bearer-token authentication code. A bearer token is a digital pass: whoever presents it is treated as the logged-in user, if the token is valid. Surface extensions need to verify these passes when a request comes in, but they should not know the private signing secret that makes the passes trustworthy.

Instead of implementing token logic here, this file imports selected names from `ufo.auth.bearer` and exposes them again as part of `ufo.sdk.bearer`. That matters because it gives extension authors one stable, intended place to import from, while keeping the real authentication implementation centralized. If the project changes how tokens are checked internally, this SDK layer can continue to provide the same public shape.

The key idea is separation of duties. The control plane creates, or “mints,” tokens. Extensions only verify them. The comment also notes an important safety rule: verification functions resolve `UFO_TOKEN_SECRET` themselves, so extensions pass in a token but do not receive or store the secret key. Like a door scanner that can tell whether a badge is valid without knowing how to print badges, this file lets extensions check access without gaining power to create access.


### `core/src/ufo/sdk/subjects.py`

`data_model` · `cross-cutting`

This file is a public doorway to subject names used by the UFO SDK. A “subject” here means an audience label: it answers the plain question, “Who is this row or message disclosed to?” For example, something may be disclosed to the whole shared workspace, or only to one member. Conversations use the same kind of labels to describe their audience, so code that decides who can see something can rely on these shared building blocks.

The file does not create new rules itself. Instead, it re-exports carefully chosen items from `ufo.turns.subjects`: the shared subject constant, the prefix used for member-specific subjects, a helper that builds a member subject, and a predicate that checks whether a subject is the shared one. This is like putting frequently used tools at the front desk: the tools live elsewhere, but SDK users do not need to know the deeper internal path to get them.

Without this file, outside code would either import from a more internal module or duplicate the subject strings by hand. That would make the public API less stable and increase the chance of small spelling mismatches in access-control labels.


### `core/src/ufo/sdk/surface_token.py`

`io_transport` · `cross-cutting`

This file is a small doorway for public SDK users. A “surface token” is a permanent link-style token tied to a surface, meaning a surface can create and check its own link addresses without directly holding the deployment’s secret token key. That separation matters because it keeps sensitive signing secrets in the authentication system while still giving surfaces the tools they need.

The project keeps `ufo.sdk` as a set of thin named modules instead of putting code in `__init__.py` files. This file follows that rule. It imports `mint_surface_token` and `verify_surface_token` from `ufo.auth.surface_token` and re-exports them under the same names. In everyday terms, it is like a reception desk that points callers to the right office while keeping the public entrance easy to remember.

Without this file, users of the SDK would have to know the internal authentication module path, which would make public code more tightly tied to the project’s private layout. By re-exporting these functions here, the project can offer a cleaner public API while leaving the actual security work in the auth module.
