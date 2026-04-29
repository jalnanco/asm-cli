# QA Matrix

`asm-cli` 릴리스 전에 어떤 기능을 QA해야 하는지와, 현재 `tests/asm_selftest.py`가 어디까지 자동 커버하는지를 한눈에 보기 위한 문서입니다.

## Legend

- `Covered`: 대표 happy path와 핵심 regression이 자동화돼 있음
- `Partial`: 중요한 일부만 자동화돼 있고, 명령/플랫폼/에이전트 조합이 비어 있음
- `Gap`: 현재 자동화가 없어서 수동 QA에 의존함

## Quick Smoke Suite

빠르게 "지금도 기본 동작이 살아 있나"만 보려면 아래를 우선 돌립니다.

```sh
make smoke
```

이 명령은 `./bin/asm smoke-test` 를 실행하며, 아래 핵심 normal path만 추립니다.

- `doctor`
- session `list`
- Claude/Codex `resume`
- `inspect`
- Claude trust preflight
- `handoff --to claude`

릴리스 전 최종 확인은 여전히 `make test` 가 기준입니다.

## Command / Feature Matrix

| Area | What to QA | Automated coverage | Notes |
| --- | --- | --- | --- |
| CLI bootstrap | `help`, unknown subcommand, usage text, prompt text | Covered | `test_help_flag`, `test_unknown_subcommand_shows_usage`, prompt/header tests |
| `doctor` | shell integration, dependency listing, iTerm2 detect fallback | Covered | Darwin + `pgrep`/AppleScript fallback 모두 있음 |
| Session discovery | Codex/Claude/OpenCode 세션 정렬, exact lookup, unindexed Codex session 발견 | Covered | `test_list_prefers_recent_claude_over_old_opencode`, `test_codex_lookup_is_exact`, `test_list_includes_recent_unindexed_codex_session` |
| Agent coverage breadth | Cursor Agent, Gemini 실제 session scan 결과 | Gap | 현재 self-test seed가 Codex/Claude/OpenCode 중심 |
| Resume / shell integration | TTY/non-TTY resume 동작, shell integration command replay | Covered | `test_resume_without_wrapper_*`, `test_shell_integration_replays_nested_commands` |
| Revive flow | `asm revive`가 preview/action 경로에서 exact resume/clone/handoff를 수행하는지 | Gap | 현재 `resume`은 있으나 `revive` 명령 end-to-end는 없음 |
| Metadata | pin toggle, merged alias/tag/tab payload, active tab fallback | Partial | pin/merge/fallback은 있음. alias/tag 입력 mutation 자체는 없음 |
| Tabs | active tab filter, header count, prompt state | Partial | filtering은 있음. tab create/rename/delete/assign flow는 없음 |
| Preview / transcript | list preview, inspect transcript, excerpt truncation | Partial | inspect/export는 있음. preview helper와 truncation edge는 직접 테스트 없음 |
| `inspect` | bundle fallback, transcript presence, command block | Covered | `test_inspect_falls_back_without_helper` |
| `export` | light bundle 생성, reopen command, excerpt 포함 | Covered | `test_export_contains_recent_excerpt_and_reopen_command` |
| `handoff` | deep preview sections, source cwd hint로 target cwd 결정 | Covered | `test_deep_handoff_preview_contains_deep_sections`, `test_handoff_prefers_project_hint_when_codex_cwd_is_home` |
| `absorb` | deep handoff wrapper 기본값 유지 | Covered | `test_absorb_defaults_to_deep_handoff_command` |
| Handoff option breadth | `--no-open`, `--print-bundle`, `--include-files`, `--include-transcript-path`, target별 codex/claude/gemini 조합 | Partial | 핵심 path는 있으나 flag/target 조합 전수는 없음 |
| QMD index | `index status`, `index update` existing collection preservation | Covered | `test_index_status_reports_qmd_sync_details`, `test_index_update_does_not_remove_existing_qmd_collection` |
| iTerm import/save | current tab import, `codex resume --last` 보존, autosave lock cleanup, current-window extract | Covered | import/save/autosave/extract regression 존재 |
| iTerm restore/open | `iterm-restore`, tab/window open, clone current 동작 | Gap | snapshot 생성 쪽은 있으나 restore execution은 없음 |
| Cache / render internals | cache file 생성, active cache missing, render merge, ANSI off | Covered | internal helper regression이 꽤 촘촘함 |
| Standalone helper packaging | helper missing 오류 메시지 | Covered | runtime/store helper missing 둘 다 있음 |
| `init` / `setup` | shell init snippet, profile write idempotency | Partial | `init`은 shell replay로 간접 확인. `setup` file mutation은 없음 |
| `self-test` command | self-test command 자체가 실패 시 non-zero/summary를 올바르게 내는지 | Gap | 실제 CI에서는 돌지만 command contract 자체는 별도 assertion 없음 |

## Current Release Bar

현재 자동화 기준으로는 아래 범위까지는 비교적 신뢰할 수 있습니다.

- 기본 CLI 진입과 `doctor`
- Codex/Claude/OpenCode 중심 session discovery
- `resume`, `inspect`, `export`, `handoff`, `absorb`
- QMD index status/update
- iTerm snapshot 생성 관련 핵심 regression
- 캐시/merge/render 내부 유틸리티

## Manual QA Checklist

자동화가 비어 있는 영역은 릴리스 전에 최소 한번 수동 확인하는 편이 안전합니다.

1. `asm revive <ref>` 후 `resume`, `clone`, `handoff` 선택이 각자 예상 명령을 실행하는지
2. picker에서 alias/tag를 실제 입력했을 때 SQLite 메타와 list output이 같이 반영되는지
3. `asm handoff --no-open`, `asm handoff --print-bundle`, `asm export --include-files --include-transcript-path` 조합이 의도대로 동작하는지
4. Cursor Agent / Gemini 세션이 실제 로컬 환경에서 list/inspect 대상에 잘 들어오는지
5. `asm iterm-restore <name>` 와 `asm iterm-clone-current` 가 실제 iTerm2에서 창/탭을 복원하는지
6. `asm setup` 이 기존 셸 설정 파일을 안전하게 수정하고 중복 삽입하지 않는지

## Recommended Next Tests

우선순위가 높은 다음 자동화 후보는 이 순서입니다.

1. `revive` end-to-end action selection regression
2. alias/tag mutation persistence regression
3. `iterm-restore` snapshot replay regression
4. Cursor Agent / Gemini session discovery fixtures
5. `setup` idempotent profile update regression
