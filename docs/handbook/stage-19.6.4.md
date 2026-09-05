# iMessage chat, group, and poll protobuf types  `stage-19.6.4`

This stage is shared behind-the-scenes support for the iMessage extension. It does not start the app or run the main chat workflow by itself. Instead, it provides the standard data shapes that other parts of the system use when they talk about conversations. These files are generated from Protocol Buffers, a compact format for describing structured data so different programs can read and write it the same way.

The chat types file is the general vocabulary for iMessage chat records and chat-level events. It gives the rest of the code ready-made Python classes for those records. The group types file focuses on changes inside group chats, such as people joining or leaving, or the group name being updated. The poll types file does the same for polls, covering options, votes, and poll change events.

Together, these files act like labeled forms. Other code fills them in, sends them, stores them, or reads them, knowing every part has the same expected meaning.

## Files in this stage

### Conversation Protobuf Types
Generated protobuf modules defining shared iMessage conversation structures for chats, group changes, and polls.

### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/chat_types_pb2.py`

`generated` · `import time and data serialization/deserialization`

This file is machine-made from a Protocol Buffers definition, which is a language-neutral way to describe structured data. In plain terms, it defines the “forms” the system uses when talking about an iMessage chat: its unique ID, display name, participants, unread count, last message, and whether it is archived or filtered. It also defines small event shapes for things that can happen to a chat, such as being marked read, archived, unarchived, or having its background changed.

The file matters because different parts of the system need to agree exactly on what a chat record looks like. Without this shared definition, one part might send a chat field that another part does not understand. Protocol Buffers help prevent that by acting like a standardized shipping label for data.

Most of the visible code registers a serialized description of these message shapes with Google’s protobuf runtime. The runtime then builds the actual Python classes at import time. This file also pulls in related message definitions, such as timestamps, address information, and message details, because a chat can include participants and a last message. The warning at the top is important: people should not edit this file by hand. Changes should be made in the original `.proto` source file and regenerated.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/group_types_pb2.py`

`generated` · `cross-cutting serialization and data exchange`

This file is machine-made from a Protocol Buffers definition. Protocol Buffers, often called protobuf, are a way to describe structured data once and then generate code that can safely read and write that data in many programming languages. In everyday terms, this file is like a set of pre-printed forms for group chat events: each form says exactly which fields can appear and what kind of information belongs in each field.

The messages here describe specific iMessage group changes. For example, `GroupDisplayNameChanged` stores a new display name, while `GroupParticipantAdded`, `GroupParticipantRemoved`, and `GroupParticipantLeft` store information about the person involved. There are also empty marker messages for icon changes and icon removals, where the important fact is simply that the event happened.

The central message is `GroupChangeEvent`. It ties a change to a chat identifier, a time, an optional actor, and a flag saying whether the current user caused it. It then allows exactly one kind of change to be attached, such as a participant being added or the icon being removed. That “only one of these” shape prevents confusing records like one event claiming to be both a name change and a participant removal.

Because this is generated code, people normally should not edit it by hand. The source `.proto` file should be changed instead, then this file regenerated.


### `extensions/imessage/ufo_ext_imessage/proto/photon/imessage/v1/poll_types_pb2.py`

`generated` · `cross-cutting: used whenever poll protobuf messages are imported, serialized, or decoded`

This file is machine-made from a Protocol Buffers definition. Protocol Buffers, often called protobuf, are a way for different parts of a system to agree on the exact shape of data so it can be stored or sent between services safely. Here, the agreed data shape is for iMessage polls: what the poll is called, what options it has, who voted, and what kind of poll change happened.

The file does not contain hand-written business logic. Instead, it registers a serialized schema with Google’s protobuf runtime, then builds Python classes from that schema. Those classes include messages such as PollOption, PollInfo, PollParticipantVote, and PollChangeEvent. Think of it like a printed form template: the template says which boxes exist, such as “title” or “option identifier,” and the rest of the program fills those boxes in.

It also imports related message definitions, including timestamps and address information, because poll events need to say when they happened and who performed them. The PollChangeEvent message is especially important because it can represent different kinds of changes, such as a poll being created, an option being added, a vote being cast, or a vote being removed. Without this file, Python code in this extension would not know how to construct or decode poll-related protobuf messages.
