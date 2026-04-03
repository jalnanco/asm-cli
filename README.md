# asm-cli

`asm` is a macOS-first session manager for local AI coding agents.

It scans local session stores for tools such as Codex, Claude Code, OpenCode, Cursor Agent, and Gemini, then gives you a single `fzf`-based TUI to resume, pin, tag, group into tabs, export handoff bundles, and reopen work in iTerm2.

## Status

This is usable, but it is still closer to a power tool than a polished cross-platform product.

Current scope:

- macOS only
- zsh / bash shell integration
- `fzf` TUI
- iTerm2 integration is optional
- local session file/database scanning only

## Features

- Unified session list across multiple local AI CLIs
- Pin, alias, and tag metadata stored separately from original sessions
- Custom tabs for grouping sessions
- Session previews and transcript excerpts
- Handoff/export bundles for cross-agent continuation
- iTerm2 reopen / restore helpers
- `doctor` and `self-test` commands

## Requirements

- macOS
- `zsh` or `bash`
- `fzf`
- `jq`
- `sqlite3`
- `python3`
- `rg`
- `pbcopy` for clipboard actions
- `osascript` and iTerm2 for iTerm-specific features

Run:

```sh
./bin/asm doctor
```

## Install

Clone the repository and link or copy the launcher into your `PATH`.

```sh
mkdir -p ~/.local/bin
ln -sf "$(pwd)/bin/asm" ~/.local/bin/asm
```

Then enable shell integration:

```sh
eval "$(asm init zsh)"
```

Or add it permanently:

```sh
asm setup
```

## Development

Run diagnostics:

```sh
make test
./bin/asm doctor
```

`make test` runs the isolated self-test suite behind `./bin/asm self-test`. The regression harness stubs iTerm2 process detection and AppleScript responses, so it stays deterministic and does not require a live iTerm2 session.

Basic usage:

```sh
./bin/asm
./bin/asm list
./bin/asm help
```

Before publishing or opening a PR, the minimum validation bar should be:

- `make test`
- `./bin/asm doctor`

## Design Notes

- Original agent session files are not modified.
- `asm` keeps its own metadata in `~/.local/share/asm/meta.sqlite`.
- Cache files and exports also live under `~/.local/share/asm/`.
- The TUI is optimized for interactive terminal use; non-interactive output disables ANSI color when `NO_COLOR=1` or stdout is not a TTY.

## Privacy

`asm` reads local session files and local databases from supported tools. It does not require a remote service for normal operation.

Before publishing screenshots, logs, or exported bundles, review them for prompts, code paths, repository names, and other sensitive context.

## Compatibility

Supported now:

- Codex
- Claude Code
- OpenCode
- Cursor Agent
- Gemini

Best-effort / platform-specific:

- iTerm2 window and tab restore
- importing currently open iTerm sessions

## License

This repository is released under the MIT License. See [LICENSE](LICENSE).

## Attribution

This project was inspired by [`subinium/agf`](https://github.com/subinium/agf), which is MIT licensed.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the upstream notice and attribution guidance.

## Disclaimer

This project is not affiliated with Anthropic, OpenAI, Google, Anysphere, or any other supported tool vendor.
