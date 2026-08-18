# Identity, credentials, access, seats, and safety contracts  `stage-19.4`

This stage is shared behind-the-scenes support for extensions and outside code. It creates safe public “front doors” into identity, access, and safety tools, so callers do not need to depend on private internal modules that may move.

The audience and subjects SDK files describe who can see a conversation or row. Audience gives public access to conversation-audience names and helpers. Subjects exposes helpers for shared-workspace visibility and member-specific visibility. Bearer provides a safe way to verify bearer tokens, which are login/session tokens carried with a request, without exposing the secret used to make them. Credentials exposes approved credential tools. Grants re-publishes connection and permission-audit tools, so extensions can inspect access in a stable way.

Operator exposes helpers for operator-only web sessions. Seats re-exports seat types and helpers, used to track or reason about allowed user places. Untrusted shares the common marker for text that should be treated carefully, like input from outside users. Together these files form a controlled SDK layer around access and safety.

## Files in this stage

### Audience doorway
Public audience helpers are exposed first as the SDK entry point for conversation-audience visibility.

### `core/src/ufo/sdk/audience.py`

`other` · `cross-cutting`

This file does not define new behavior. Instead, it republishes audience-related values and helpers from the internal `ufo.audience` module. An “audience” here means the intended visibility or recipient group for a conversation item, such as a shared audience, a room audience, or a foreign-room audience. Think of it like labels on envelopes: the real label-making rules live elsewhere, but this file puts the label tools on the public counter where SDK users can find them.

Its main job is to create a stable boundary between the public SDK and the project’s internal layout. Code outside the project can import names like `Audience`, `parse_audience`, or `conversation_audience` from this SDK module. If the internal module structure changes later, this file can continue to present the same public names, which helps avoid breaking users.

Without this file, SDK users would likely need to import from `ufo.audience` directly. That would expose internal organization details and make the public interface more fragile. Because it only re-exports existing constants, types, and functions, there are no local functions to document here.


### Access and credential doorways
Bearer verification, credentials, grants, and operator session helpers provide stable public imports for authentication and authorization workflows.

### `core/src/ufo/sdk/bearer.py`

`util` · `request handling`

This file is a small public wrapper around the project’s bearer-token checking tools. A bearer token is like a stamped wristband: if it is real and unexpired, the holder can prove they were admitted by the gateway. The gateway or control plane creates these tokens, but extensions only need to verify them. This file helps keep that boundary clear.

Rather than defining new behavior, it re-exports a few names from `ufo.bearer`: the login path, the session cookie name, and helper functions for checking a token and reading its claims. A claim is a piece of trusted information inside the token, such as which workspace the user belongs to.

The important safety idea is that extensions do not receive or store the signing secret. The verification functions resolve `UFO_TOKEN_SECRET` themselves inside the core code. That means an extension can ask, “Is this token valid?” without being handed the private key that could create tokens. Without this file, extension authors would either need to import deeper internal code directly or risk copying token-checking logic, which would make the system harder to keep consistent and safe.


### `core/src/ufo/sdk/credentials.py`

`other` · `cross-cutting`

This file does not create new credential behavior itself. Instead, it re-exports a carefully chosen set of names from the internal `ufo.credentials` module. In plain terms, it is like a reception desk: outsiders do not walk through every office in the building, but they can ask for the approved services at the front desk.

The problem it solves is API clarity and safety. Extensions need to work with credentials, but the project likely does not want them depending directly on every internal detail of the credential implementation. By putting approved credential classes, errors, and helper functions here, the SDK gives extension authors a stable place to import from.

The exported items include credential storage, functions for opening an installation, functions for naming or authorizing credential slots, and specific error types for invalid requests, invalid values, or failed credential creation. If this file were missing, extension code might have to import from internal modules directly, making extensions more fragile when the project changes its internal layout.


### `core/src/ufo/sdk/grants.py`

`other` · `import time / extension API use`

This module is a small doorway into the project’s grant and connection auditing features. In this codebase, `ufo.sdk` is meant to be the public software development kit: the part outside extensions should import from. Because the project does not allow code inside `__init__.py` files, the SDK is split into named modules like this one.

The real work lives in `ufo.grants`. That lower-level module defines things such as connection records, summaries of granted permissions, and helper functions for listing main-agent connections. This file imports those selected names and exposes them again under `ufo.sdk.grants`.

The practical value is stability and clarity. An extension can write imports against the SDK path instead of reaching into internal project modules directly. That is like using a building’s front desk instead of wandering through staff-only corridors: the same information may be available, but the public route is safer and more intentional. If this file were missing, extension code would either break or need to depend on internal paths that may change more easily.


### `core/src/ufo/sdk/operator.py`

`other` · `cross-cutting, during operator-only request handling`

This file is like a signpost at the front of a building: it points callers to the right operator-session tools without making them know where those tools live internally. The project has web pages or debug tools that are meant only for operators, and those tools need the same checks every time: decide which workspace an operator is trying to use, and bind the shared operator session cookie after a POST request. Rather than making every caller import from the deeper `ufo.ext.operator` module, this file exposes those pieces through `ufo.sdk.operator`, which is a more public-facing import path. That matters because internal module layouts can change over time. By re-exporting here, the project can keep a stable doorway for SDK users or other project code while still keeping the real implementation elsewhere. If this file disappeared, code that imports these operator helpers from the SDK path would break, even though the underlying logic still exists.


### Participation scopes
Seat helpers and subject visibility names define who participates in workspaces and who can see rows or conversations.

### `core/src/ufo/sdk/seats.py`

`other` · `cross-cutting`

This file is like a clearly labeled front desk for seat-reporting features. The real seat logic lives deeper in `ufo.seats`, but outside code should not have to know that internal path. Instead, extensions can import from `ufo.sdk.seats`, which is part of the public software development kit, or SDK: the supported interface meant for other code to use.

It exposes four things: `SeatEntry`, `Seats`, `SeatSnapshot`, and `member_workspaces`. These represent the seat state, safe ways to write or inspect it, and a helper for building workspace membership information. The important point is that this file keeps the rules in the core system while letting extensions decide when to apply them.

Without this file, extension code would need to import directly from internal modules. That would make the project harder to reorganize later, because changing the internal location of seat logic could break outside users. By re-exporting these names here, the project creates a stable public doorway while preserving freedom to change the rooms behind it.


### `core/src/ufo/sdk/subjects.py`

`data_model` · `cross-cutting`

This file is a small public doorway into the project’s subject system. A “subject” here means an audience label: for example, something visible to the whole shared workspace, or something visible to one specific member. Other parts of the system can attach these labels to rows of data or conversation audiences to decide who is allowed to see them.

The file does not create new rules itself. Instead, it re-exports a few names from the internal `ufo.subjects` module: the shared subject value, the member subject prefix, and helper functions for making or recognizing these subject labels. This is like putting commonly used tools on a front desk so callers do not need to walk into the back room and depend on the internal layout.

Why this matters: code outside the core implementation can import from `ufo.sdk.subjects` and stay insulated from where the subject logic actually lives. If the internal module moves or changes shape later, this SDK-facing file can preserve the public import path. Without it, outside users would have to rely directly on internal project structure, making their code more fragile.


### Safety marking
The shared untrusted-content marker gives SDK users and internal code the same safety contract for tainted text.

### `core/src/ufo/sdk/untrusted.py`

`util` · `cross-cutting`

Some text that enters the system should not be treated as trusted instructions. Examples include a tool's printed output, a response from an outside provider, or content returned by a third-party extension. This file exists to make the boundary around that kind of text consistent.

It does one small but important thing: it imports `wall` from `ufo.untrusted` and exposes it through the SDK path as `ufo.sdk.untrusted.wall`. In plain terms, `wall` is a shared marker or wrapper that says, “the content inside here came from outside; do not treat it as the system’s own instructions.” The analogy is a quarantine label on a package: the package can still be opened and read, but everyone knows it should not be blindly trusted.

Without this file, SDK extensions might invent their own way to fence off risky output, or import from deeper internal modules. That would make safety behavior harder to keep consistent. By re-exporting the core definition here, the SDK gives outside code a stable, official doorway to the same untrusted-content boundary used inside the main system.
