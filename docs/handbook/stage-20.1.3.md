# SDK credentials, grants, subjects, and seat access exports  `stage-20.1.3`

This stage is shared behind-the-scenes support for extension developers. It does not run the main product flow itself. Instead, it provides safe public “front doors” into internal access and identity tools, so extensions do not need to depend on hidden module paths that may change.

The credentials module exposes approved helpers and error types for working with stored credentials. The authproxy module exposes the credential shapes and constants used by feed-sync connectors that get their login material through an authentication proxy. The bearer module exposes only the checking side of bearer tokens, which are login tokens carried with a request; extensions can verify a token made by the gateway, but cannot create one themselves. The grants module exports tools for inspecting connections and auditing grants, meaning records of who was allowed to access what. The subjects module exports standard visibility “subjects,” the labels used to decide which people can see a disclosed row. The seats module exports helpers for seat-related access, such as user entitlement or capacity checks. Together, these files form a stable SDK surface over the runtime access system.

## Files in this stage

### Credential Doorways
Public SDK re-export modules for connector credentials, bearer-token verification, and general credential helpers.

### `core/src/ufo/sdk/authproxy.py`

`other` · `extension setup and credential resolution`

This file does not define new behavior. Its job is to give extension authors a stable, simple place to import the pieces needed to plug in a credential backend. A credential backend is the part of the system that answers the question, “What login secret or token should this connector use for this source?”

The file exposes four names from deeper runtime modules: `AuthProxy`, `Credential`, `DIRECT_ACCOUNT`, and `AuthProxySpec`. In plain terms, `AuthProxy` is the interface an extension implements, `Credential` is the result it returns, `DIRECT_ACCOUNT` marks sources whose credential comes directly from the member rather than from a connected broker account, and `AuthProxySpec` describes the backend in the extension manifest.

This matters because extensions should not need to know the project’s internal folder layout. Like a front desk in a large building, this file points outsiders to the right official entry point instead of sending them through private corridors. If it disappeared, extension code could still possibly import the deeper modules, but it would become more tightly coupled to internals and more likely to break when the runtime is reorganized.


### `core/src/ufo/sdk/bearer.py`

`other` · `request handling`

This module is a small public wrapper around the real bearer-token code in `ufo.harness.auth.bearer`. A bearer token is like a temporary wristband: whoever presents it can be recognized, but the system must still check that it is genuine and unexpired. Surface extensions need to do that checking, but they should not know the signing secret that creates valid tokens. This file solves that by exposing the safe checking tools while leaving token minting under the control plane’s ownership.

It re-exports route and cookie names such as `LOGIN_PATH`, `LOGOUT_PATH`, and `SESSION_COOKIE`, plus helper functions such as `verify_token`, `verified_claims`, and `workspace_claim`. The important detail is that these functions resolve the secret internally from `UFO_TOKEN_SECRET`; callers pass in a token, not a secret. That keeps the key inside core code instead of spreading it into extension code.

Without this file, extensions would either import from a deeper internal module, which makes the public boundary unclear, or risk being given too much authority. This module acts like a reception desk: it points outsiders to the right verification services without handing them the master key.


### `core/src/ufo/sdk/credentials.py`

`other` · `cross-cutting; active when extensions import SDK credential helpers`

This file is like a small front desk for credential features in the SDK. The real credential code lives deeper inside the project, under the runtime access layer. Instead of asking extension authors to import from those internal paths, this file exposes only the credential pieces they are meant to use.

That matters because internal project layout can change over time. If outside code imports directly from deep runtime modules, it becomes fragile. By importing from `ufo.sdk.credentials`, extensions get a clearer promise: these are the supported credential names available to them.

The exported items include error types for invalid credential requests and invalid credential values, a `CredentialStore` type, and helper names such as `credential_object_name` and `deploy_env`. In plain terms, these are the building blocks an extension may use when asking for secrets or environment-bound credential information.

There are no functions or classes defined here. Every line simply imports a name from `ufo.runtime.access.credentials` and makes it available under the SDK namespace. If this file were missing, extension code would either fail to import these public credential tools or would have to depend on private internal paths.


### Access and Visibility Exports
Public SDK re-export modules for grant auditing, seat-related access helpers, and visibility subjects.

### `core/src/ufo/sdk/grants.py`

`other` · `cross-cutting import-time public API`

This module is like a front desk for a deeper part of the system. The real logic for recording and summarizing connection permissions lives in `ufo.runtime.access.grants`, but outside code should not need to know that internal path. Instead, extension code can import from `ufo.sdk.grants`, which is part of the public software development kit, or SDK — the set of supported tools meant for other developers to use.

The file re-exports audit-related classes and functions. These include summaries of connection attempts, summaries of granted permissions, records of successful connections, and an error used when a connection is denied. It also exposes helper functions that turn internal grant records into easier-to-read summaries.

This matters because public import paths are a promise. If internal code is reorganized later, the project can keep `ufo.sdk.grants` working while changing where the real implementation lives. Without a module like this, extension authors might import directly from internal runtime files, making their code more fragile.

A notable detail is that the package avoids putting code in `__init__.py` files, so public SDK names are gathered into small named modules like this one instead.


### `core/src/ufo/sdk/seats.py`

`other` · `cross-cutting import-time public API`

This file is a small doorway into the project’s seat-reporting features. A “seat” here means a person or account that may count toward product access, licensing, or membership reporting. The real rules and implementations live in `ufo.runtime.seats`, but outside code is not expected to reach directly into that internal runtime area.

Instead, this module exposes the approved names through `ufo.sdk.seats`. That matters because extensions can import from a stable software development kit, or SDK, path while the project keeps freedom to reorganize its internal code later. It is like a reception desk: visitors ask here for `Seats`, `SeatEntry`, or helper functions such as `member_by_email`, and the desk points them to the correct internal place.

The file re-exports the seat state container, individual seat entries, and helper functions for common questions: finding a member by email, checking whether a member is an admin, listing a member’s workspaces, and finding a workspace domain. Nothing is calculated in this file itself. Its job is to keep the public API clear and intentional.


### `core/src/ufo/sdk/subjects.py`

`data_model` · `cross-cutting`

This file is a small public doorway into subject-related helpers that live deeper in the runtime code. In this project, a “subject” means the audience a piece of disclosed data is meant for, such as everyone in a workspace or one specific member. You can think of it like the label on an envelope: the label says who is allowed to read what is inside.

The actual definitions come from `ufo.runtime.turns.subjects`. This file imports those definitions and exposes them again under the SDK path. That matters because outside code should not need to know the runtime’s internal folder layout. If the internal implementation moves later, SDK users can keep importing from this stable file.

The exported pieces cover the common audience labels: `SHARED_SUBJECT` for workspace-shared data, `MEMBER_SUBJECT_PREFIX` for member-specific labels, `member_subject` for building a member-specific subject, and `subject_shared` for checking whether a subject is the shared one. The module docstring also explains that conversation audiences use the same atoms, so code deciding who may see rows and code describing who is in a conversation can speak the same simple language.
