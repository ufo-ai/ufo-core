#!/bin/sh
# usage: curl -fsSL https://ufo.ai/ufo | sh
# installs the native ufo client for this platform into $UFO_HOME/bin and runs it.
set -eu

UFO_URL="${UFO_URL:-https://ufo.ai}"
export UFO_URL
UFO_HOME="${UFO_HOME:-$HOME/.ufo}"
BIN="$UFO_HOME/bin/ufo"

say() { printf '%s\n' "$*"; }
die() { printf 'ufo: %s\n' "$*" >&2; exit 1; }

DIM='' RESET=''
if [ -t 1 ] && [ "${TERM:-dumb}" != dumb ]; then
  DIM="$(printf '\033[2m')" RESET="$(printf '\033[0m')"
fi

OS=$(uname -s)
ARCH=$(uname -m)
case "$OS" in
  Darwin)
    case "$ARCH" in
      arm64) TARGET=aarch64-apple-darwin ;;
      x86_64) TARGET=x86_64-apple-darwin ;;
      *) die "no ufo client is built for $OS/$ARCH" ;;
    esac
    ;;
  Linux)
    case "$ARCH" in
      x86_64) TARGET=x86_64-unknown-linux-musl ;;
      aarch64 | arm64) TARGET=aarch64-unknown-linux-musl ;;
      *) die "no ufo client is built for $OS/$ARCH" ;;
    esac
    ;;
  MINGW* | MSYS* | CYGWIN*)
    die "on Windows, download $UFO_URL/ufo/bin/x86_64-pc-windows-msvc and save it as ufo.exe"
    ;;
  *) die "no ufo client is built for $OS/$ARCH" ;;
esac

profile_file() {
  case "${SHELL:-/bin/sh}" in
    */zsh) printf '%s' "${ZDOTDIR:-$HOME}/.zshrc" ;;
    */bash)
      if [ "$OS" = Darwin ]; then
        printf '%s' "$HOME/.bash_profile"
      else
        printf '%s' "$HOME/.bashrc"
      fi
      ;;
    */fish) printf '%s' "$HOME/.config/fish/conf.d/ufo.fish" ;;
    *) printf '%s' "$HOME/.profile" ;;
  esac
}

add_to_path() {
  case ":$PATH:" in *":$UFO_HOME/bin:"*) return 0 ;; esac
  case ":$PATH:" in
    *":$HOME/.local/bin:"*)
      mkdir -p "$HOME/.local/bin"
      ln -sf "$BIN" "$HOME/.local/bin/ufo"
      say "${DIM}✓ Linked ufo into ~/.local/bin${RESET}"
      return 0
      ;;
  esac
  bin_ref="$UFO_HOME/bin"
  case "$bin_ref" in "$HOME"/*) bin_ref="\$HOME${bin_ref#"$HOME"}" ;; esac
  profile=$(profile_file)
  line="export PATH=\"$bin_ref:\$PATH\""
  case "$profile" in *.fish) line="fish_add_path -g \"$bin_ref\"" ;; esac
  if [ -f "$profile" ] && grep -Fq "$bin_ref" "$profile"; then
    return 0
  fi
  mkdir -p "$(dirname "$profile")"
  printf '\n# added by ufo installer\n%s\n' "$line" >> "$profile"
  say "${DIM}✓ Added ufo to PATH in $profile${RESET}"
  say "${DIM}  For this shell:${RESET} export PATH=\"$UFO_HOME/bin:\$PATH\""
}

mkdir -p "$UFO_HOME/bin"
STAGED="$BIN.tmp.$$"
curl -fsSL "$UFO_URL/ufo/bin/$TARGET" -o "$STAGED" || die "Could not fetch $UFO_URL/ufo/bin/$TARGET"
chmod +x "$STAGED"
mv "$STAGED" "$BIN"
say "${DIM}✓ Installed ufo ($BIN)${RESET}"
add_to_path
exec "$BIN" "$@"
