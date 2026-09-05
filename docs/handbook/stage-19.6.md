# iMessage extension provider and generated protocol plumbing  `stage-19.6`

This stage is behind-the-scenes plumbing for the iMessage extension. It is not the public face of the SDK and it is not the code that chooses how to handle a conversation. Instead, it supplies the contract, message shapes, and network wiring that let the extension talk to the rest of the system in a predictable way.

The provider.py file is the hand-written center of this stage. It defines what an iMessage-like provider must be able to do, and the simple records used for incoming messages, attachments, and provider events.

Around it is generated Protocol Buffers code, which provides shared “forms” for data. One group supplies Google API support needed by the generated files. Other groups define iMessage data shapes for messages, media, chats, groups, polls, and service requests. The gRPC files add the call wiring, so clients and servers can send those forms across process or network boundaries. Together, these pieces work like a standardized plug and socket set for the iMessage extension.

## Sub-stages

- [Google API protobuf support for iMessage extension](stage-19.6.1.md) `stage-19.6.1` — 4 files
- [iMessage protobuf service descriptor modules](stage-19.6.2.md) `stage-19.6.2` — 4 files
- [iMessage core message and media protobuf types](stage-19.6.3.md) `stage-19.6.3` — 6 files
- [iMessage chat, group, and poll protobuf types](stage-19.6.4.md) `stage-19.6.4` — 3 files
- [iMessage generated gRPC service wiring](stage-19.6.5.md) `stage-19.6.5` — 4 files

## Files in this stage

### iMessage extension provider and generated protocol plumbing
### `extensions/imessage/ufo_ext_imessage/provider.py`

`data_model` · `cross-cutting`

This file is like the plug shape for the iMessage extension. The rest of the system does not need to know whether messages come from a local database, a remote service, or another bridge. It only needs a provider that follows this contract.

The small frozen data classes describe the information that moves across that boundary. A MessageAttachment names a file attached to a message. An InboundMessage represents a received message, including who sent it, which conversation it belongs to, its text, any attachments, and whether it was direct. A ProviderEvent wraps message-stream progress: it can carry a new message, a sequence number, or the current head sequence. A sequence number is a marker used to know “how far through the message stream we are,” like a bookmark in a book.

MessageProvider is a Protocol, meaning it is an interface: it says what methods a real provider must offer, but it does not implement them here. The surface layer calls these methods to catch up on old messages, subscribe to live ones, send replies or attachments, download files, and classify provider errors. Without this file, different provider implementations could disagree on names, inputs, or expected results, and the iMessage surface would not have a reliable way to communicate with them.

#### Function details

##### `MessageProvider.installation_id`  (lines 37–37)

```
def installation_id(self) -> str
```

**Purpose**: This property gives the unique identifier for the provider installation. The rest of the system can use it to tell one configured message source apart from another.

**Data flow**: The caller asks the provider for its installation identity → the provider returns a string identifier → no message data is changed.

**Call relations**: No direct caller is shown in the provided graph. In the broader contract, this is available whenever code needs to label or distinguish the active provider.


##### `MessageProvider.assign_line`  (lines 39–39)

```
async def assign_line(self, phone_number: str, idempotency_key: str) -> str
```

**Purpose**: This method asks the provider to assign or connect a phone number line. The idempotency key helps make repeated attempts safe, so the same request can be retried without accidentally creating duplicate work.

**Data flow**: A phone number and retry-safe idempotency key go in → the provider performs whatever setup its backend requires → a string result comes back, likely identifying the assigned line or operation.

**Call relations**: No direct caller is shown in the provided graph. It is part of the provider contract for setup or provisioning flows that need to connect a phone number before messaging can work.


##### `MessageProvider.catch_up`  (lines 41–41)

```
def catch_up(self, after_sequence: int | None) -> AsyncIterator[ProviderEvent]
```

**Purpose**: This method produces past provider events after a known sequence marker. It is used when the system starts or reconnects and needs to read anything it missed.

**Data flow**: The caller gives the last sequence number it has already processed, or nothing if it has no bookmark → the provider streams events after that point → the caller receives ProviderEvent objects one by one through an asynchronous iterator.

**Call relations**: extensions/imessage/ufo_ext_imessage/surface.ImessageSurface._catch_up calls this when the surface needs to fill the gap between its saved position and the provider’s current messages.

*Call graph*: called by 1 (_catch_up).


##### `MessageProvider.subscribe`  (lines 43–43)

```
def subscribe(self, ready: asyncio.Event) -> AsyncIterator[ProviderEvent]
```

**Purpose**: This method opens a live stream of new provider events. It lets the system keep listening after the catch-up phase is done.

**Data flow**: The caller gives an asyncio.Event, which is a small signal object used by asynchronous code → the provider starts a live subscription and can mark the signal when it is ready → new ProviderEvent objects come out over time.

**Call relations**: extensions/imessage/ufo_ext_imessage/surface.ImessageSurface._pump_live calls this to receive new messages as they happen.

*Call graph*: called by 1 (_pump_live).


##### `MessageProvider.send_text`  (lines 45–45)

```
async def send_text(self, conversation_id: str, text: str, idempotency_key: str) -> str
```

**Purpose**: This method sends a plain text message into a conversation. The idempotency key makes retries safer if the first attempt’s result is unclear.

**Data flow**: A conversation id, message text, and idempotency key go in → the provider sends the text through its underlying messaging service → it returns a string, typically an identifier for the sent message or operation.

**Call relations**: extensions/imessage/ufo_ext_imessage/surface.ImessageSurface._prove calls this when the surface needs to send a text response through the provider.

*Call graph*: called by 1 (_prove).


##### `MessageProvider.send_attachment`  (lines 47–53)

```
async def send_attachment(self, conversation_id: str, filename: str, data: bytes, idempotency_key: str) -> str
```

**Purpose**: This method sends a file attachment into a conversation. It is used when the system needs to deliver binary content, not just text.

**Data flow**: A conversation id, filename, raw file bytes, and idempotency key go in → the provider uploads or sends the attachment through its backend → it returns a string identifier for the sent attachment or operation.

**Call relations**: extensions/imessage/ufo_ext_imessage/surface.ImessageSurface._send_contact_card calls this when it needs to send a contact card file through the message provider.

*Call graph*: called by 1 (_send_contact_card).


##### `MessageProvider.download_attachment`  (lines 55–55)

```
def download_attachment(self, attachment_id: str) -> AsyncGenerator[bytes, None]
```

**Purpose**: This method downloads an attachment by its provider attachment id. It returns the file data in chunks, which avoids needing to hold the whole file in memory at once.

**Data flow**: An attachment id goes in → the provider finds and reads that attachment → chunks of bytes come out through an asynchronous generator until the file is complete.

**Call relations**: extensions/imessage/ufo_ext_imessage/surface.ImessageSurface._downloaded_files calls this when it needs to turn message attachment references into actual downloaded file contents.

*Call graph*: called by 1 (_downloaded_files).


##### `MessageProvider.invalidate`  (lines 57–57)

```
async def invalidate(self) -> None
```

**Purpose**: This method tells the provider that its current state or connection should be considered no longer valid. A real implementation might use this to clear cached credentials, close sessions, or force a reconnect.

**Data flow**: The caller gives no extra data → the provider invalidates whatever internal state it owns → nothing is returned except completion of the asynchronous operation.

**Call relations**: No direct caller is shown in the provided graph. It exists as part of the contract so higher-level code has a standard way to mark a provider unusable or stale.


##### `MessageProvider.invalid_cursor`  (lines 59–59)

```
def invalid_cursor(self, error: Exception) -> bool
```

**Purpose**: This method asks whether an error means the saved stream position is no longer usable. A cursor is a bookmark into the provider’s event stream.

**Data flow**: An exception goes in → the provider checks whether that error represents a bad or expired cursor → it returns true or false.

**Call relations**: No direct caller is shown in the provided graph. It is available for recovery code that needs to decide whether to discard its old sequence bookmark and resync.


##### `MessageProvider.external_error`  (lines 61–61)

```
def external_error(self, error: Exception) -> bool
```

**Purpose**: This method asks whether an exception came from the outside messaging provider rather than from local code. That distinction helps the surface decide how to report or recover from failures.

**Data flow**: An exception goes in → the provider classifies it according to its backend’s error rules → it returns true if the error should be treated as an external provider error, otherwise false.

**Call relations**: extensions/imessage/ufo_ext_imessage/surface.ImessageSurface._consume_connected, extensions/imessage/ufo_ext_imessage/surface.ImessageSurface._downloaded_files, and extensions/imessage/ufo_ext_imessage/surface.ImessageSurface._send_contact_card call this when they catch failures and need to decide how to interpret them.

*Call graph*: called by 3 (_consume_connected, _downloaded_files, _send_contact_card).


##### `MessageProvider.error_code`  (lines 63–63)

```
def error_code(self, error: Exception) -> str
```

**Purpose**: This method turns a provider-specific exception into a short error code. That gives higher-level code a stable label to log, return, or branch on without understanding every backend’s exception details.

**Data flow**: An exception goes in → the provider extracts or chooses an appropriate code → a string error code comes out.

**Call relations**: extensions/imessage/ufo_ext_imessage/surface.ImessageSurface._send_contact_card calls this after an attachment-sending failure so it can describe the provider error in a consistent way.

*Call graph*: called by 1 (_send_contact_card).
