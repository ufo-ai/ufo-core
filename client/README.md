# ufo (terminal client)

This crate builds `ufo`, the terminal program a person uses to talk to their workspace assistant.
Run it in a terminal, type a message, and the reply is rendered where you typed — text, diffs, code
with syntax colors, and tool activity. Left, on an empty entry bar, lists every conversation you can
open — from the terminal, the portal, or Slack — under the mark, with the entry bar still at the
bottom: typing there and pressing Enter starts a new chat, Up reaches the search line and then the
list, and Enter on a row opens it. A click opens a row too, Esc returns to the conversation you
left, and the list refreshes every five seconds while it is up, with a row drawn bold once its
conversation moves. The page opens on the list as it last stood, kept under `$UFO_HOME`, and
refreshes behind it.

The client is also the assistant's hands on your machine. When the assistant needs to read a file,
write one, edit one, search a tree, or run a command, the request arrives over the same connection
and the client carries it out in your current working directory, then sends the result back. Nothing
else has to be installed for that: the binary is self-contained, and it is built for macOS, Linux,
and Windows.

It picks its renderer from the terminal it finds. An interactive terminal gets the full-screen
interface, which opens under the drawn mark with this build's version beside it; a pipe, a dumb
terminal, or `UFO_PLAIN` gets plain lines; `--json` reads and writes JSON events on stdin and stdout so another program can
drive it. `--remote` leaves the current directory on the member's machine and runs the conversation
in the workspace's configured sandbox.

```
ufo [--resume ID] [--remote] [--json] [message...]
ufo login | logout
```

Sign-in happens in the conversation itself the first time you run it: answer the prompts, and the
client keeps the credential, workspace, and system skill cache under `$UFO_HOME` (`~/.ufo` by
default). `ufo login` starts sign-in over, and `ufo logout` forgets it.

`ufo` with no message opens on the list of your chats. `--resume` with a channel resumes one of
your own terminal conversations, and with a conversation id joins a conversation from any surface.
A terminal conversation runs in the directory you are in; a joined conversation keeps the sandbox it
already has, so file and command requests in it never reach your machine.

## Run it in development

Prerequisite: a stable Rust toolchain. Release builds also need Go 1.27.0.

```bash
cd client
cargo run -- --help
cargo run -- "hello"
```

With no environment set the client talks to the hosted service, so a development run needs a
workspace to talk to. Point it at a local stack (`make stack` from the repository root prints the
gateway URL it serves) and keep its credentials out of your real ones:

```bash
UFO_HOME=/tmp/ufo-dev UFO_URL=http://ufo-1.localhost:18080 cargo run -- "hello"
```

The release embeds `gh` for its target:

```bash
scripts/build-gh.sh aarch64-apple-darwin /tmp/ufo-gh.gz
UFO_GH_ARCHIVE=/tmp/ufo-gh.gz cargo build --release
```

CI builds that release for each supported platform.

| Variable | What it does |
|---|---|
| `UFO_URL` | Base URL of the gateway the client signs in against. Defaults to the hosted service. |
| `UFO_HOME` | Directory holding the credential, current workspace, input history, and system skill cache. Defaults to `~/.ufo`. |
| `WORKSPACE_URL` | Talks to one workspace directly, instead of the one stored after sign-in. |
| `UFO_CHANNEL` | Names the conversation to join instead of starting a fresh one. |
| `UFO_PLAIN` | Any value forces the plain line renderer. `NO_COLOR` and `TERM=dumb` do the same. |

A change in this folder needs the crate version in `Cargo.toml` raised in the same change: installed
clients update themselves only when the version moves, and CI fails a pull request that forgets it.

## Run the tests

```bash
cd client
cargo test
```

That is everything, and it needs no network, no services, and no credentials. The suite covers the
in-process behavior plus end-to-end sessions: those start a scripted gateway on loopback, run the
built binary against it, and check what it renders and what it sends back, in each of the three
modes.

The static gates, which CI also runs:

```bash
cargo fmt --check
cargo clippy --all-targets -- -D warnings
```
