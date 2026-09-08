## Keyed providers

These providers authenticate with a workspace API key rather than a connected account, so `list_external_tools` does not list them and `connect_account` cannot reach them. Their state is visible as credential slots: list the `credential` object kind to see which are filled.

To connect one, run the `credential` collection's `request_credentials` action for its slots (a workspace admin fills them privately — never ask for a key in chat prose), then call the provider's own REST API from the sandbox. Each slot exports an env var holding a sentinel, not the secret: the egress proxy swaps in the real key on the wire, so the sandbox never holds it and the raw value cannot be read, echoed, or written to a file. An env var that is absent means the slot is empty — read the `credential` object kind, which reports each slot filled or empty and the host a keyed slot resolves to here, then ask the member for what is missing rather than guessing a key or a host.

{{providers}}
