# iMessage generated domain message types  `stage-20.2.3`

This stage is shared behind-the-scenes support for the iMessage extension. It does not start the system or run the main work by itself. Instead, it provides the standard data shapes that other code uses when it talks about iMessage records. These files are generated from Protocol Buffers, a format for turning structured data into compact messages that programs can store or send.

Each file covers one part of the iMessage world. The address types describe people or contact addresses. Attachment types describe shared files, images, and related details. Chat types describe conversations and chat events. Group types record group-chat changes, such as members joining, leaving, or names changing. Message types are the largest vocabulary, covering texts, reactions, stickers, edits, read receipts, deleted messages, and attachments. Poll types describe poll options, votes, and changes. The streaming file defines a small heartbeat message, like a regular “still here” signal, to keep a live connection open. Together, these files act like labeled containers so the rest of the system can exchange iMessage data consistently.

## Files in this stage

### Participant and asset records
Foundational generated protobuf types for iMessage participants and attached media or files.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/address_types_pb2.py`

`generated` · `cross-cutting serialization and message parsing`

This file is machine-made from a Protocol Buffers definition file, not handwritten project code. Protocol Buffers, often called protobuf, are a compact way for different parts of a system to agree on the shape of data they send to each other. In everyday terms, this file is like a printed form template: it says what fields an address record can have, and what choices are allowed for the chat service type.

The data described here covers two address shapes. One is for an address tied to a single service, such as iMessage or SMS. The other is for an address that may work with several services. Both can also include an optional country value. The file also defines the allowed service names: unspecified, iMessage, SMS, and RCS.

At import time, the protobuf runtime reads the encoded schema stored in this file and builds the usable Python message classes and enum values. Other code can then create these message objects, fill in fields, serialize them for storage or transport, or parse them back from bytes. Because this is generated code, developers should edit the source `.proto` file instead; changing this file directly would be fragile and would likely be overwritten.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/attachment_types_pb2.py`

`generated` · `cross-cutting serialization and message parsing`

This file is produced automatically from a Protocol Buffers schema, which is a shared recipe for how data should be laid out when systems send it to each other or save it. In plain terms, it teaches Python what an iMessage attachment record looks like: its unique id, file name, file type, size, transfer status, and special details such as whether it is a sticker or part of a Live Photo.

The file registers that recipe with Google’s protobuf runtime. The runtime then builds usable Python message classes from the embedded schema data. Other code can create an AttachmentInfo message, fill in fields like mime_type or total_bytes, and serialize it into compact bytes. Later, another part of the system can turn those bytes back into the same structured attachment information.

It also defines small fixed choice lists, called enums, for values such as TransferState and CompanionKind. These prevent code from passing vague strings around for important states like pending, failed, or finished.

Because this is generated code, people should not edit it directly. If the attachment data shape needs to change, the source .proto file should be changed and this Python file regenerated. Without this file, Python code in this project would not know how to interpret these attachment messages safely or consistently.


### Conversation content and events
Generated protobuf types for chats, group changes, messages, and polls that make up iMessage conversation history.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/chat_types_pb2.py`

`generated` · `cross-cutting serialization and data exchange`

This file is machine-made from a Protocol Buffers definition file, not written by hand. Protocol Buffers, often called protobuf, are a compact way for programs to describe data so it can be saved or sent between systems without everyone inventing their own format. Here, the data is about iMessage chats: the chat itself, who participates, the last message, and events such as a chat being archived, unarchived, marked read, or having its background changed.

The file first checks that the installed protobuf Python runtime is the expected version. That matters because generated protobuf code and the runtime library need to agree on how messages are built and interpreted. It then imports related message definitions, such as timestamps, addresses, and messages, because a chat includes people and may include its latest message.

The large serialized block is the compact recipe for all the message shapes in this file. The protobuf builder reads that recipe and creates usable Python classes such as `Chat` and `ChatChangeEvent`. Think of it like a cookie cutter: the recipe defines the shape, and the generated classes let the rest of the program make actual chat records in that shape.

Because this file is generated, developers normally should not edit it directly. Changes should be made in the source `.proto` file and regenerated.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/group_types_pb2.py`

`generated` · `cross-cutting serialization and data exchange`

This file is machine-made from a Protocol Buffers definition file. Protocol Buffers, often called protobuf, are a compact way for different parts of a system to agree on the shape of data they send to each other. In everyday terms, this file is like a pre-printed form for recording group chat changes: it says which boxes exist, what kind of value goes in each box, and which boxes are mutually exclusive.

The messages here describe specific group events. A group display name change stores the new name. Participant added, removed, and left events store the person involved, using address information defined in another generated file. Icon changed and icon removed events are marker messages, meaning the event type itself is the important fact and there is no extra detail stored here.

The main message is GroupChangeEvent. It ties a change to a chat identifier, a time, an optional actor, whether the local user caused it, and exactly one concrete change type. That “exactly one” choice is represented by protobuf’s oneof feature, which works like a form section where only one checkbox can be selected at a time.

Because this file is generated, developers should not edit it directly. If the group event format needs to change, the source .proto file should change and this file should be regenerated.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/message_types_pb2.py`

`generated` · `cross-cutting serialization and data exchange`

This file is produced automatically from a Protocol Buffers definition. Protocol Buffers, often called protobuf, are a way to describe data in a strict, language-neutral format so different parts of a system can agree on exactly what a “message” looks like. Think of it like a standardized form: every service fills in the same boxes, so the next service can read it without guessing.

The file registers many iMessage-related data types with Google’s protobuf runtime. These include small building blocks such as a reaction, a mention, a text formatting span, an attachment reference, and sticker placement. It also defines larger records such as MessageContent and Message, which combine those pieces with timestamps, sender details, delivery flags, reply and reaction metadata, and chat identifiers. Near the end, it defines event-shaped records for changes over time, such as a message being received, edited, read, unsent, reacted to, or given a sticker.

There is very little hand-written behavior here. The important work is the schema: it lets the rest of the project create Python message objects, serialize them into compact bytes, and deserialize bytes back into reliable Python objects. Without this file, Python code in this extension would not know the exact fields or enum values expected for iMessage data exchanged through the protobuf layer.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/poll_types_pb2.py`

`generated` · `import time and any time poll data is serialized or parsed`

This file is machine-made from a Protocol Buffer definition file. Protocol Buffers are a compact, structured way for programs to agree on the shape of data they send or store. In everyday terms, this file is like a set of pre-printed forms for iMessage polls: one form for a poll option, one for a participant’s vote, one for the full poll, and one for changes such as a poll being created or someone voting.

The file does not contain hand-written business logic. Instead, it registers a serialized description of the poll message formats with Google’s protobuf runtime. The runtime then builds usable Python classes from that description at import time. Those classes include `PollOption`, `PollParticipantVote`, `PollInfo`, `PollCreated`, `PollOptionAdded`, `PollVoted`, `PollUnvoted`, and `PollChangeEvent`.

It also connects poll data to related message types from other protobuf files, such as timestamps and iMessage address information. Without this file, Python code in this extension would not know the agreed field names and structure for poll-related iMessage data, so it could not reliably decode incoming poll records or encode poll updates for storage or transport.


### Streaming keepalive
Generated protobuf type for maintaining live iMessage streaming connections.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/streaming_pb2.py`

`generated` · `import time and streaming protocol communication`

This file is not handwritten project logic. It is produced by the Protocol Buffers compiler, which turns a `.proto` definition file into Python code. Protocol Buffers, often called protobuf, are a compact way for different programs to agree on the exact shape of messages they send to each other.

Here, the source definition is `photon/imessage/v1/streaming.proto`. The generated code registers that definition with Google’s protobuf runtime and builds the Python message classes from it. The important message in this file is `Heartbeat`, an empty message type. Even though it carries no fields, it still matters: sending a heartbeat is like saying “I’m still here” on a long-lived connection. Without a shared `Heartbeat` message definition, different parts of the system could disagree about how to encode or recognize that keep-alive signal.

At import time, the file also checks that the installed protobuf runtime is new enough for the generated code. Then it loads a serialized description of the message schema into the protobuf descriptor pool, which is the runtime’s registry of known message types. Other code can then import this module and create, serialize, or parse `Heartbeat` messages without needing to know the low-level wire format.
