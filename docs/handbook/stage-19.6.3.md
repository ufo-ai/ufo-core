# iMessage core message and media protobuf types  `stage-19.6.3`

This stage is shared behind-the-scenes support for the iMessage extension. It does not send messages by itself. Instead, it provides the common shapes that other parts of the system use when they talk about iMessage data. These shapes are generated from Protocol Buffers, a format for defining structured data so different pieces of software can read and write it the same way.

The two __init__.py files are simple package markers. They make the photon and iMessage protocol folders importable in Python, like putting labels on drawers so the rest of the code can find what it needs.

The address types module defines how iMessage addresses and supported chat services are represented. The attachment types module defines records for media and extras, such as files, stickers, and Live Photo videos. The message types module defines the main message and event records. The streaming module defines the Heartbeat record used to keep a message stream alive. Together, these files act like standardized forms that the rest of the system fills in, reads, and passes around.

## Files in this stage

### Package namespace markers
Package marker files establish the Photon iMessage protocol namespace so generated protobuf modules can be imported normally.

### `extensions/imessage/ufo_ext_imessage/proto/photon/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder often needs an `__init__.py` file to be treated as an importable package, meaning other files can refer to code inside it using dotted names such as `proto.photon.something`. Think of it like a label on a drawer: the label does not store the contents, but it tells the rest of the system that the drawer exists and can be opened in an organized way.

Because this file is empty, it does not define settings, functions, classes, or startup behavior. Its value is structural. Without it, some Python environments or tools might not recognize `extensions/imessage/ufo_ext_imessage/proto/photon` as a package, which could make imports fail or behave inconsistently. This matters especially for generated protocol code or message definitions, where many small modules need to be found reliably by the rest of the iMessage extension.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/__init__.py`

`other` · `import time`

This file does not contain executable code, but it still has a practical job. In Python, an `__init__.py` file tells Python that a folder should be treated as a package, meaning its contents can be imported as a named group. Here, the package sits under the iMessage extension’s generated protocol area, likely alongside files created from protocol definitions. A protocol definition is a shared message format that different parts of a system use to agree on what data looks like.

Without this file, some Python tooling or older Python import behavior might not recognize this directory as an importable package. That could make imports fail for code that expects to load modules from `extensions.imessage.ufo_ext_imessage.proto.photon.imessage.v1`.

You can think of it like a label on a filing cabinet drawer. The drawer may hold the real documents, while this label simply makes the drawer findable by name. Its presence helps the rest of the project refer to this versioned iMessage protocol namespace cleanly and consistently.


### Core iMessage data types
Generated protobuf modules define the shared address, attachment, and message structures used by the iMessage protocol implementation.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/address_types_pb2.py`

`generated` · `import time and message serialization/deserialization`

This file is machine-made from a Protocol Buffers definition, so humans are not meant to edit it directly. Protocol Buffers, often called protobuf, are a compact way for programs to agree on the shape of data they send to each other or save. Here, the agreed data is about messaging addresses: an address can be tied to one service, like iMessage or SMS, or to several possible services.

The file registers these data shapes with Google’s protobuf runtime when Python imports it. After that, other code can create and read messages such as `SingleServiceAddressInfo` and `MultiServiceAddressInfo`. These messages carry fields like the address string, the chat service type, and an optional country. The service type is limited to known values: unspecified, iMessage, SMS, or RCS. That matters because it prevents different parts of the system from inventing their own spellings or meanings for the same idea.

An everyday analogy is a printed form: this file defines the boxes on the form and the allowed choices in a dropdown. Other parts of the project fill out the form, send it, store it, or read it back. Without this file, Python code would not know how to build or decode these specific address messages.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/attachment_types_pb2.py`

`generated` · `cross-cutting`

This file is machine-generated from an attachment_types.proto file. Protocol Buffers, often called protobuf, are a compact way for different programs to agree on the shape of data they exchange. In everyday terms, this file is like a pre-printed form: it defines what boxes exist for attachment information, what kind of value each box can hold, and which named choices are allowed.

The main message described here is AttachmentInfo. It represents metadata about an iMessage attachment: its unique id, file name, MIME type, Apple-style file type identifier, size, transfer status, and flags such as whether it is outgoing, hidden, or a sticker. It also includes optional companion information, which is useful for cases like Live Photos, where an image may have a related video file.

The file also defines smaller companion-related records and two enums, which are fixed lists of named values. TransferState says whether an attachment is pending, transferring, failed, finished, unavailable, or unknown. CompanionKind says what kind of companion file is attached.

Project code should not edit this file directly. If the attachment data format needs to change, the source .proto file should be changed and this Python file regenerated. Without this generated file, Python parts of the system would not have the concrete classes and constants needed to serialize, deserialize, or inspect iMessage attachment data safely.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/message_types_pb2.py`

`generated` · `cross-cutting: used whenever iMessage message data is created, read, serialized, or deserialized`

This file is produced by the Protocol Buffers compiler, not written by hand. Protocol Buffers, often called protobuf, are a compact format for defining data shapes once and then using them safely across different programs and languages. In everyday terms, this file is like a set of standardized forms for iMessage data: one form for a message, another for a reaction, another for an edit event, another for a sticker placement, and so on.

The file registers the message schema with Google’s protobuf runtime and builds Python classes from it. Other parts of the project can then create, read, serialize, and deserialize these message objects without manually parsing raw bytes or guessing field names. The schema covers message text, attachments, formatting, mentions, mini-app content, sender information, delivery and read times, reactions, stickers, edits, unsent messages, and change events.

It also imports related generated schemas for timestamps, addresses, and attachments, because a full message needs those pieces too. Without this file, Python code in this extension would not know the exact structure of an iMessage message payload, and data exchanged with other services could easily become inconsistent or unreadable. Because it is generated, changes should be made in the original `.proto` schema file, then this file should be regenerated.


### Streaming heartbeat type
The streaming protobuf module provides the generated heartbeat message type used by the iMessage stream protocol.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/streaming_pb2.py`

`generated` · `import time and message serialization`

This file is not handwritten project logic. It was produced by the Protocol Buffers compiler from `photon/imessage/v1/streaming.proto`. Protocol Buffers, often called protobuf, are a compact way for different programs to agree on the shape of messages they send to each other. Here, the agreed message is a very small one named `Heartbeat`.

A heartbeat message is like a quick “still here?” signal on a connection. Systems often send these on a stream so both sides know the connection is alive even when no real user data is moving. Without this generated file, Python code in this project would not have the concrete `Heartbeat` class and metadata needed to create, parse, or recognize that protobuf message.

At import time, the file checks that the installed protobuf runtime is compatible with the version used to generate the code. It then registers the serialized protocol description with protobuf’s shared descriptor pool. Finally, protobuf’s internal builder creates the Python message classes and descriptors from that description. Because this is generated code, developers should edit the `.proto` source file instead of changing this file directly.
