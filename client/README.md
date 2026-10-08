# ufo (terminal client)

This crate builds `ufo`, the terminal program a person uses to talk to their workspace assistant.
Run it in a terminal, type a message, and the reply is rendered where you typed — text, diffs, code
with syntax colors, and tool activity. Left, on an empty entry bar, lists every conversation you can
open — from the terminal, the portal, or Slack — under the mark, with the entry bar still at the
bottom: typing there and pressing Enter starts a new chat, Up reaches the search line and then the
list, and Enter on a row opens it. A click opens a row too, Esc returns to the conversation you
left, and the list refreshes every five seconds while it is up. Each row leads with its state: a
spinner while the agent works, a green dot where the conversation moved since you last read it, an
amber one where it waits on you. The page opens on the list as it last stood, kept under
`$UFO_HOME`, and refreshes behind it.

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
ufo [--resume ID] [--remote] [--wait SECONDS] [--authorize ID CHOICE] [--json] [message...]
ufo login | logout
ufo proxy --session TOKEN [--env | --stop]
```

A program that runs `ufo` in a pipe reads plain lines and needs no other mode. stdout is the
conversation: the reply, shared files as links, and each question with the command that answers it —
`ufo --resume <channel> "<answer>"`, or for a permission question
`ufo --resume <channel> --authorize <id> allow|deny|always` — after which the process exits. stderr
names `Resume: ufo --resume <channel>` as soon as the message is accepted. A turn can run for hours,
and a conversation started in a directory runs its file and command steps through this process, so
the client stays until the turn ends. `--wait` leaves early and needs `--remote`, where the turn
needs no running client; `ufo --resume` prints the rest.

The client talks to the workspace in `WORKSPACE_URL` or `$UFO_HOME/workspace` (`~/.ufo` by default)
with the token in `$UFO_HOME/credentials`. With no workspace it signs in through a gateway, in the
conversation itself: answer the prompts, and the client keeps the credential, workspace, and gateway
under `$UFO_HOME`. With no gateway either, it exits naming the workspace file. `ufo login` starts
sign-in over, and `ufo logout` forgets it.

`ufo` with no message opens on the list of your chats. `--resume` with a channel resumes one of
your own terminal conversations, and with a conversation id joins a conversation from any surface.
A terminal conversation runs in the directory you are in; a joined conversation keeps the sandbox it
already has, so file and command requests in it never reach your machine.

`ufo proxy --session TOKEN` puts a program on this machine behind a proxy service session. It reads
the session's environment from the proxy service, writes `$UFO_HOME/proxy/ca.pem` (this machine's
roots plus the proxy's CA), starts a loopback daemon that relays to the proxy over TLS (reusing one
that already relays to the same proxy service and replacing one that relays elsewhere), and prints
the variables to set: the four proxy variables pointing at the daemon, `NO_PROXY`, each binding's
sentinel, and the CA bundle for every tool that reads one. `--env` prints them as `export` lines for `eval "$(ufo proxy --session TOKEN --env)"`, and `--stop` ends the daemon.

## Run it in development

Prerequisite: a stable Rust toolchain. Release builds also need Go 1.27.0.

```bash
cd client
cargo run -- --help
cargo run -- "hello"
```

A development run needs a workspace to talk to. Point it at a local serve (`make serve` from the
repository root) and keep its credentials out of your real ones:

```bash
mkdir -p /tmp/ufo-dev && install -m 600 ~/.ufoctl/token /tmp/ufo-dev/credentials
UFO_HOME=/tmp/ufo-dev WORKSPACE_URL=http://localhost:8710 cargo run -- "hello"
```

The release embeds `gh` for its target:

```bash
scripts/build-gh.sh aarch64-apple-darwin /tmp/ufo-gh.gz
UFO_GH_ARCHIVE=/tmp/ufo-gh.gz cargo build --release
```

CI builds that release for each supported platform. At build time `UFO_GATEWAY_URL_DEFAULT` names
the sign-in gateway a client with nothing stored reaches, and `UFO_PROXY_URL_DEFAULT` names the proxy
service `ufo proxy` reaches; this repository's build sets neither.

| Variable | What it does |
|---|---|
| `UFO_URL` | Base URL of the sign-in gateway, ahead of the one stored after sign-in and the build's default. |
| `UFO_PROXY_URL` | Base URL of the proxy service `ufo proxy` reaches, ahead of the build's default. |
| `UFO_HOME` | Directory holding the credential, current workspace, input history, system skill cache, and `ufo proxy` state. Defaults to `~/.ufo`. |
| `WORKSPACE_URL` | Talks to one workspace directly, instead of the one in `$UFO_HOME/workspace`. |
| `UFO_CHANNEL` | Names the conversation to join instead of starting a fresh one. |
| `UFO_PLAIN` | Any value forces the plain line renderer. `NO_COLOR` and `TERM=dumb` do the same. |

A change in this folder needs the crate version in `Cargo.toml` raised in the same change: a deploy
that serves the client tells an installed one to update only when the version moves, and CI fails a
pull request that forgets it. Such a deploy sets `UFO_CLIENT_VERSION` and `UFO_CLIENT_BINARY_URL` on
`serve`, and each target's binary downloads from `{UFO_CLIENT_BINARY_URL}/{target}`.

## Run the tests

```bash
cd client
cargo test
```

That is everything, and it needs no network, no services, and no credentials. The suite covers the
in-process behavior plus end-to-end sessions: those start a scripted server on loopback, run the
built binary against it, and check what it renders and what it sends back, in each of the three
modes.

The static gates, which CI also runs:

```bash
cargo fmt --check
cargo clippy --all-targets -- -D warnings
```
