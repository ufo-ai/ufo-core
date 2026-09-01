# iMessage Generated Domain Protocol Types  `stage-18.6`

This stage is behind-the-scenes support for the iMessage extension. It does not start the system or run the main message loop by itself. Instead, it provides the shared “forms” that other code fills in when it needs to send, store, or understand iMessage data. These files are generated from Protocol Buffers, or protobufs, which are rules for packing data into a consistent machine-readable shape.

The address types file describes contact addresses and whether they can use iMessage, SMS, or RCS. The attachment types file describes shared details for sent files, such as names, file types, transfer state, and Live Photo companion data. The chat types file represents chats and changes to chats. The group types file focuses on group chat membership and other group-change records. The message types file covers messages, reactions, stickers, edits, read states, and related events. The poll types file describes poll options, votes, and poll updates. The streaming file defines a small heartbeat message, like a pulse, so streaming connections can show they are still alive.

## Files in this stage

### Address and Attachment Primitives
Foundational protobuf types describe contacts and transferable media used by higher-level iMessage records.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/address_types_pb2.py`

`generated` · `cross-cutting serialization/import time`

This file is machine-made glue code for Protocol Buffers, often called protobuf: a compact, structured way for programs to exchange data. The original human-written definition lives in `address_types.proto`; this Python file is the generated version that the application can import and use.

The file defines message shapes for address information. `SingleServiceAddressInfo` describes one address, the chat service tied to it, and optionally a country. `MultiServiceAddressInfo` describes one address that may support several chat services. The shared `ChatServiceType` list names the possible service kinds: unspecified, iMessage, SMS, and RCS.

At startup, the protobuf runtime checks that the installed Python protobuf library is compatible with the version used to generate this file. Then it registers the serialized schema with protobuf’s descriptor pool, which is like a catalog of message blueprints. Finally, protobuf’s internal builder creates the Python classes and enum values from that blueprint.

Nothing in this file contains hand-written business rules. Its importance is compatibility: if it were missing or out of sync with the `.proto` definition, code that sends, receives, stores, or decodes these address records could fail or misunderstand the data.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/attachment_types_pb2.py`

`generated` · `cross-cutting serialization and data exchange`

This file is machine-made from a Protocol Buffers definition. Protocol Buffers, often called protobuf, are a compact format for describing data so different parts of a system can exchange it without guessing what each field means. In everyday terms, this file is like a standardized form for iMessage attachment information: every attachment has named boxes such as its unique ID, file name, media type, size, and transfer state.

The file registers several message shapes with Google’s protobuf runtime. `AttachmentInfo` describes the main attachment record, including whether it was sent or received, whether it is hidden, whether it is a sticker, and whether it has related companion content. `CompanionInfo` describes metadata for an extra paired file, and `Companion` can carry the actual companion bytes plus what kind of companion it is. The enums `TransferState` and `CompanionKind` give fixed names to known status values, such as pending, failed, finished, or Live Photo video.

Because this is generated code, humans should not edit it directly. If it were missing or out of sync with its `.proto` source file, code that serializes or deserializes iMessage attachment data could fail, misread fields, or lose information.


### Conversation Structure
Chat and group-change schemas define how conversations and membership updates are represented.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/chat_types_pb2.py`

`generated` · `import time and data serialization/deserialization`

This file is machine-made glue between a data definition file and Python. The original definition, `chat_types.proto`, describes what a chat looks like: its unique ID, display name, whether it is a group, its participants, unread count, last message, and similar details. It also describes chat-level events, such as a chat being archived, unarchived, marked read, or having its background changed.

Protocol Buffers, often called protobuf, are a compact data format used so different programs can agree on the exact shape of information. Think of the `.proto` file as a blank form, and this generated Python file as the stack of ready-to-use form classes Python code can fill in, inspect, send, or save.

At import time, the file checks that the installed protobuf runtime is compatible, loads related message definitions such as timestamps, addresses, and messages, then registers the chat message types with protobuf's central descriptor pool. Most project code should not edit this file directly. If the chat data shape needs to change, the source `.proto` file should change and this file should be regenerated. Without it, Python code in this extension would not know how to construct or decode these chat records in the agreed protobuf format.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/group_types_pb2.py`

`generated` · `cross-cutting serialization and data exchange`

This file is machine-generated from a Protocol Buffers schema, which is a compact, language-neutral way to describe structured data. In everyday terms, it is like a preprinted form for group chat events: who was added, who left, whether the group name changed, whether the icon changed, and when it happened.

The file registers these message shapes with Google's protobuf runtime. Other code can then use classes such as GroupChangeEvent, GroupParticipantAdded, or GroupDisplayNameChanged without manually parsing raw bytes or dictionaries. The most important message is GroupChangeEvent. It records the chat identifier, the time the change happened, whether the local user caused it, an optional actor, and exactly one kind of change. That “exactly one” choice is represented by protobuf's “oneof” feature, meaning a single event cannot simultaneously be both a name change and a participant removal.

It also imports shared message types: Timestamp for dates and times, and SingleServiceAddressInfo for people or accounts involved in the group change. Because this file is generated, humans should not edit it directly. Changes should be made in the source .proto file, then regenerated.


### Message and Poll Events
Message and poll protobuf modules capture the primary user-facing events exchanged within chats.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/message_types_pb2.py`

`generated` · `cross-cutting`

This file is like a pre-printed set of forms for describing iMessage activity. Instead of each part of the system inventing its own shape for a message, this generated code defines standard Python message classes such as Message, MessageContent, MessageReaction, StickerPlaced, and MessageChangeEvent. Protocol Buffers, often called protobuf, is a Google data format that lets programs turn structured objects into compact bytes and back again. That matters when message data must move between processes, be saved, or be sent over an interface without losing meaning.

The file itself should not be edited by hand. It is generated from message_types.proto by the protobuf compiler. At import time, it checks that the installed protobuf runtime is compatible, imports related definitions for timestamps, addresses, and attachments, then registers a serialized schema with protobuf’s descriptor pool. A descriptor is the blueprint that says which fields exist, what type each field has, and which fields are optional or repeated.

The generated builder then creates the actual Python classes and enums from that blueprint. The main ideas represented here are full messages, pieces of message content, formatting and mentions, attachment references, reactions, placed stickers, and event wrappers for changes like received, edited, read, unsent, reaction added, reaction removed, or sticker placed. Without this file, Python code in this extension would not know how to construct, parse, or validate these iMessage protobuf objects.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/poll_types_pb2.py`

`generated` · `cross-cutting`

This file is produced by the Protocol Buffers compiler, not written by hand. Protocol Buffers, often called protobuf, are a standard way to describe structured data so different parts of a system can agree on exactly what fields exist and how they are encoded. In everyday terms, this file is like a pre-printed form for iMessage poll information: everyone fills in the same boxes, so the data can be understood later.

The messages defined here cover the main pieces of an iMessage poll. A poll option has text and an identifier. A participant vote links a person to the option they chose. A full poll record contains the poll message ID, chat ID, title, options, and votes. There are also smaller event messages for changes over time, such as a poll being created, an option being added, someone voting, or someone removing a vote.

The most important wrapper is the poll change event. It records where and when a change happened, who performed it when known, whether it came from the current user, and which single kind of change occurred. The file also connects to shared protobuf definitions for timestamps and iMessage address information. If this file were missing, code could not reliably serialize or parse poll events from the iMessage extension.


### Streaming Heartbeats
The streaming schema provides the heartbeat message used to keep live communication channels active.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/streaming_pb2.py`

`generated` · `request handling`

This file is not handwritten application logic. It is produced by the Protocol Buffers compiler, where Protocol Buffers are a compact, structured way for programs to exchange messages across services or processes. Think of the original `.proto` file as a form template, and this generated Python file as the code Python needs in order to recognize, create, and read forms of that type.

The main job here is to register the `photon.imessage.v1` streaming message definitions with Google’s protobuf runtime. It validates that the installed protobuf library is the expected version, loads a serialized description of the message schema, and asks protobuf’s builder tools to create Python message classes from that schema.

The schema in this file contains a `Heartbeat` message. A heartbeat is usually a tiny “I am still alive” signal sent over a long-running connection, similar to tapping the table every so often so the other side knows you have not left. Without this generated file, other Python code in the iMessage extension would not be able to import or use the `Heartbeat` protobuf type correctly. Because it is generated, developers should update the source `.proto` file and regenerate this file rather than editing it by hand.
