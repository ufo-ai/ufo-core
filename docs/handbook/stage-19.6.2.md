# iMessage protobuf service descriptor modules  `stage-19.6.2`

This stage is shared behind-the-scenes support for the iMessage extension. It does not send messages by itself. Instead, it defines the “paper forms” that other code must use when talking about iMessage data. These files are generated from Protocol Buffers, a format that describes structured data so different programs can agree on exact request and response shapes.

The attachment module defines the forms for working with file attachments, such as creating or reading attachment requests and replies. The chat module defines the shapes for chat-related actions and the service description for those remote calls. The event module defines how a client asks for updates it missed, such as message, group, poll, or chat changes. The message module defines the main message API shapes, including sending, editing, listing, reacting to, and subscribing to message updates.

Together, these files act like the labeled parts bins in a workshop. Later gRPC code adds the actual client and server wiring, but these modules first define what can be sent and understood.

## Files in this stage

### iMessage service schemas
Generated protobuf descriptor modules define the attachment, chat, event, and message service contracts consumed by higher-level iMessage gRPC wiring.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/attachment_service_pb2.py`

`generated` · `import time and request/response serialization`

This file is machine-made from a Protocol Buffers definition file, which is a language-neutral contract for data sent between systems. In plain terms, it is like a shared form template: everyone who sends or receives attachment data agrees on the same fields, names, and shapes.

The attachment service described here covers three main tasks: asking for information about an attachment, uploading an attachment, and downloading an attachment. The generated code registers message types such as requests and responses for those tasks. For example, an upload request can carry a file name, raw file bytes, and optional companion-device information. A download response can carry either a header, a primary file chunk, or a companion file chunk, so large attachment data can be streamed piece by piece instead of sent all at once.

The file also records service metadata for `AttachmentService`, including an HTTP mapping for getting attachment info at `/v1/attachments/{attachment_guid}`. Other code can import this module and use the generated classes without manually parsing bytes or guessing field names.

Because this file is generated, developers should not edit it directly. If the attachment API changes, the source `.proto` file should be updated and this Python file should be regenerated.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/chat_service_pb2.py`

`generated` · `import time and API request/response serialization`

This file is produced automatically from a Protocol Buffers definition, so people should not edit it by hand. Protocol Buffers are a compact way for different programs to agree on the exact structure of messages they send to each other, like using the same blank form so both sides know where the name, address, and date go.

The file registers the chat service schema with Google’s protobuf runtime. That schema includes request and response messages for creating a chat, marking a chat as read, setting or removing a background, sharing contact info, setting typing status, fetching a chat, counting chats, checking whether a background exists, and subscribing to chat events. It also describes the ChatService remote procedure call interface: the named operations a client can ask a server to perform. Some operations have HTTP path annotations, which means the same service can be exposed through web-style endpoints such as `/v1/chats:get` as well as through protobuf-based calls.

Other generated protobuf files provide shared pieces such as chat records, message records, address types, and streaming heartbeat events. This file ties those pieces together specifically for chat-level actions. Without it, Python code in this extension would not have the generated classes and service metadata needed to build valid chat API requests or understand chat API responses.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/event_service_pb2.py`

`generated` · `request handling`

This file is produced automatically from a Protocol Buffers definition file, not written by hand. Protocol Buffers are a compact, structured way for different parts of a system to agree on what data looks like, much like a standard form everyone fills out the same way.

The real problem this file solves is synchronization. If an iMessage client has been offline or has fallen behind, it needs to ask, “What changed after the last event I saw?” This file defines that conversation. A `CatchUpEventsRequest` can include an `after_sequence`, which is the last known event number. The server then streams back `CatchUpEventsResponse` messages. Each response can carry one kind of payload: a message change, group change, poll change, chat change, a completion notice, or a heartbeat. A heartbeat is a small “still alive” signal used to keep a stream from looking dead when there are no real changes.

The file also describes the `EventService` service and its `CatchUpEvents` streaming call. The response stream ends with a completion message containing the current head sequence, telling the client where the event log now stands. Other generated protobuf files supply the detailed shapes for messages, groups, polls, chats, and streaming heartbeat data. Without this file, Python code would not know how to create, read, or validate these event service messages in a way that matches the rest of the system.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/message_service_pb2.py`

`generated` · `import time and API request/response serialization`

This file is machine-made from a Protocol Buffers definition file. Protocol Buffers, often called protobuf, are a way for different programs to agree on the exact shape of messages they send to each other, a bit like using a standardized form instead of free-form text. Without this file, Python code in this project would not know how to build or read the message-service data used by the iMessage extension.

The file registers many message types with Google’s protobuf runtime. These include requests for sending text, attachments, multipart messages, mini app cards, edits, unsends, reactions, stickers, and message lookups. It also defines responses, such as a returned message or a page of recent messages. Some fields are optional, meaning callers can tell the difference between “not provided” and “provided with a default value.”

It also describes a service named MessageService. That service lists remote actions such as SendTextMessage, GetMessage, ListChatMessages, and SubscribeMessageEvents, along with HTTP-style paths for several of them. The actual business behavior is not implemented here. This file is the contract: it tells clients and servers what data must look like so they can reliably talk to each other.
