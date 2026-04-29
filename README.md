# asm-cli

English | [한국어](README.ko.md)

`asm` is a macOS-first session manager for local AI coding agents.

It scans local session stores for tools such as Codex, Claude Code, OpenCode,
Cursor Agent, and Gemini, then gives you a single terminal UI to browse,
search, resume, inspect, tag, group, and hand off sessions.

## What It Is

`asm` exists for people who regularly jump between multiple local AI sessions
and want one place to:

- see recent sessions across tools
- resume the right session quickly
- tag and group sessions into tabs
- inspect transcript excerpts before reopening
- hand work off between agents without manual copy-paste

It is intentionally local-first. `asm` reads local session files and databases
that already exist on your machine and stores its own metadata separately.

## Current Scope

- macOS only
- terminal-first workflow
- `fzf`-based picker
- optional iTerm2 integration
- local session discovery only
- hybrid search using local metadata, exact chunk ranking, and optional `qmd`

## Highlights

- Unified session list across supported AI CLIs
- Fast picker with direct query entry: `asm <query>`
- Session metadata: pin, alias, tag, tab assignment, last opened
- Preview modes for overview, transcript, and commands
- Cross-agent handoff and export bundles
- iTerm2 reopen, snapshot save, restore, and clone helpers
- Local Jira shortcuts for "my issues", issue view, and quick open
- Isolated self-test and smoke-test workflows

## Supported Agents

Supported now:

- Codex
- Claude Code
- OpenCode
- Cursor Agent
- Gemini

Best-effort or platform-specific:

- iTerm2 window and tab restore
- import of currently open iTerm2 sessions

## Requirements

Required:

- macOS
- `zsh` or `bash`
- `fzf`
- `jq`
- `sqlite3`
- `python3`
- `rg`

Optional but recommended:

- `pbcopy` for clipboard actions
- `osascript` and iTerm2 for window, tab, and preview helpers
- `qmd` for stronger semantic reranking

Check your environment with:

```sh
./bin/asm doctor
```

## Install

### Install From GitHub Releases

```sh
curl -L https://github.com/jalnanco/asm-cli/releases/latest/download/asm-cli-0.1.1.tar.gz -o asm-cli.tar.gz
tar -xzf asm-cli.tar.gz
cd asm-cli-0.1.1
./install.sh
```

### Install From The Working Tree

```sh
make install
```

This installs:

- `asm` into `~/.local/bin/asm`
- runtime helpers into `~/.local/libexec/asm-cli`
- docs into `~/.local/share/doc/asm-cli`

Then enable shell integration:

```sh
eval "$(asm init zsh)"
```

Or write it into your shell rc file:

```sh
asm setup
```

### Install From A Release Bundle

Unpack the release archive and run:

```sh
./install.sh
```

Override the install root if needed:

```sh
PREFIX=/custom/prefix ./install.sh
```

## Setup Checklist

After installation, this is the fastest way to get to a usable setup:

1. Make sure `asm` is on your `PATH`.
2. Enable shell integration.
3. Run `asm doctor`.
4. Optionally configure Jira.
5. Optionally prepare `asm.json` presets for projects you reopen often.

Example:

```sh
export PATH="$HOME/.local/bin:$PATH"
eval "$(asm init zsh)"
asm doctor
```

Persist the `PATH` and shell integration in your shell rc file if needed:

```sh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
echo 'eval "$(asm init zsh)"' >> ~/.zshrc
```

If you use Bash:

```sh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
echo 'eval "$(asm init bash)"' >> ~/.bashrc
```

If you use Fish:

```sh
asm init fish | source
```

## Quick Start

```sh
asm doctor
asm
asm list
asm "incident handoff"
asm inspect 2
asm resume 2
```

Typical flow:

1. Run `asm` or `asm <query>`.
2. Pick a session from the list.
3. Use preview to confirm the context.
4. Press `enter` to resume, or use handoff / inspect / clone commands.

## Core Concepts

### Session Ref

Every session has a stable ref shaped like:

```text
claude:11111111-1111-4111-8111-111111111111
codex:019d470b-a125-7533-ad4e-dc1d1219a9df
```

Most commands accept either:

- a full ref
- a visible row number after `asm list`

### Picker

Running `asm` opens the interactive picker.

Useful picker keys:

- `enter`: resume selected session
- `v`: open revive action menu
- `/`: enter search mode
- `esc`: leave search mode or close picker
- `1 / 2 / 3`: switch preview mode
- `p`: toggle pin
- `t`: toggle tags
- `a`: edit alias
- `m / n / u`: move to tab / create tab / remove from tab
- `h`: choose handoff target
- `l`: reuse last handoff target
- `i`: inspect
- `r`: refresh session list

When search mode is active, printable keys are inserted into the query instead
of triggering picker actions.

### Search

There are two search entry points:

- `asm <query>`: seed the picker with a ranked query
- `/` inside the picker: interactive search mode

Search prefers exact chunk matches over scattered single-letter matches and then
falls back to metadata ranking across:

- alias
- title
- tags
- project / cwd
- session ref

When `qmd` is available, its results are used as an additional ranking signal.

## Configuration Examples

### Minimal Daily Setup

```sh
export PATH="$HOME/.local/bin:$PATH"
eval "$(asm init zsh)"
```

### Optional `qmd`

If `qmd` is installed, `asm` uses it as a semantic reranking signal on top of
its local metadata search. No extra config is required beyond making `qmd`
available on your `PATH`.

Useful commands:

```sh
asm index init
asm index update
asm index status
```

### Jira Config

```sh
asm jira config set \
  --site your-site.atlassian.net \
  --email you@example.com \
  --token <api-token> \
  --max-issues 20
```

Inspect the saved config:

```sh
asm jira config show
```

### `asm.json` Preset File

Put an `asm.json` file in a project root when you want reusable stacks:

```json
{
  "defaultOpen": "tab",
  "presets": {
    "triage": {
      "title": "Bug Triage",
      "cwd": ".",
      "open": "window",
      "sessions": [
        "codex:latest",
        "claude:latest"
      ]
    }
  }
}
```

Then use:

```sh
asm open --list
asm open triage
```

## Command Guide

### Browse And Find

```sh
asm
asm list
asm list -a
asm query "handoff timeout"
asm inspect 3
asm inspect claude:11111111-1111-4111-8111-111111111111
```

### Continue Work

```sh
asm resume 3
asm clone 3
asm revive 3
```

When `asm clone` can detect the new session after the fork exits, it records a
local parent/child relation. The picker and inspect view then show `fork<-...`
on the child and `forks:N` on the parent.

You can also record a relation manually:

```sh
asm relate codex:child-session --parent claude:parent-session
```

### Organize Sessions

```sh
asm pin 3
asm alias 3 "search ranking follow-up"
```

Tabs and tags are mainly managed from the picker, but `pin` and `alias` are
also available as direct commands.

### Handoff And Export

```sh
asm handoff --to claude 3
asm absorb --to codex 3
asm export --to portable 3
asm inspect --bundle 3
```

Handoff modes:

- `light`: metadata and recent excerpt
- `deep`: light + recent turns, next actions, decisions, and file hints

### Diagnostics And Maintenance

```sh
asm doctor
asm status --compact
asm index status
asm smoke-test
asm self-test
```

## Jira Integration

Configure Jira once:

```sh
asm jira config set \
  --site your-site.atlassian.net \
  --email you@example.com \
  --token <api-token>
```

Then use:

```sh
asm jira mine
asm jira view 2
asm jira open ASM-101
```

The picker preview can also surface a Jira key when one is detected in aliases,
titles, tags, or recent context.

## Presets

`asm open` reads `asm.json` from the current directory or its parents.

Example:

```json
{
  "defaultOpen": "tab",
  "presets": {
    "review": {
      "title": "Review Stack",
      "cwd": "workspace",
      "open": "window",
      "sessions": [
        "codex:latest",
        "claude:11111111-1111-4111-8111-111111111111"
      ]
    },
    "solo": [
      "codex:latest"
    ]
  }
}
```

Useful commands:

```sh
asm open --list
asm open review
asm open --print review
```

## Development

Run the local validation set:

```sh
make smoke
make test
make package
zsh -n bin/asm
bash -n scripts/install.sh scripts/build-release.sh
python3 -m py_compile bin/asm_runtime.py bin/asm_store.py bin/asm_inspect.py tests/asm_selftest.py
```

Notes:

- `make smoke` is the faster regression subset.
- `make test` runs the isolated self-test suite behind `./bin/asm self-test`.
- The self-test harness stubs iTerm2 and AppleScript flows so it can run
  deterministically without a live iTerm session.

For deeper QA coverage, see [docs/qa-matrix.md](docs/qa-matrix.md).

## Release Flow

```sh
make smoke
make test
make package
git tag "v$(cat VERSION)"
git push origin "v$(cat VERSION)"
```

The release workflow checks that the tag matches `VERSION`, builds a portable
tarball, and uploads the archive plus SHA256 checksum to GitHub Releases.

## Privacy

`asm` does not require a remote service for normal operation.

It reads local session files and local databases from supported tools, and
stores its own metadata under:

- `~/.local/share/asm/meta.sqlite`
- `~/.local/share/asm/session-cache.json`
- `~/.local/share/asm/exports`
- `~/.local/share/asm/iterm-layouts`

Before publishing screenshots, logs, or exported bundles, review them for
prompts, file paths, repository names, issue keys, and other sensitive context.

## Design Notes

- Original session files are never modified.
- `asm` keeps its own metadata separate from source tools.
- The picker is optimized for interactive terminal use.
- Non-interactive output disables ANSI color when `NO_COLOR=1` or stdout is not
  a TTY.

## License

This repository is released under the MIT License. See [LICENSE](LICENSE).

## Attribution

This project was inspired by [`subinium/agf`](https://github.com/subinium/agf),
which is MIT licensed.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for attribution details.

## Disclaimer

This project is not affiliated with Anthropic, OpenAI, Google, Anysphere, or
any other supported tool vendor.
