# asm-cli

[English](README.md) | 한국어

`asm`은 로컬 AI 코딩 에이전트 세션을 한곳에서 관리하기 위한
macOS 중심 세션 매니저입니다.

Codex, Claude Code, OpenCode, Cursor Agent, Gemini 같은 도구의 로컬
세션 저장소를 스캔해서, 하나의 터미널 UI 안에서 세션을 검색하고,
이어서 작업하고, 태그를 붙이고, 탭으로 묶고, inspect/handoff/export
할 수 있게 해줍니다.

## 무엇을 위한 도구인가

`asm`은 여러 AI CLI를 동시에 쓰는 사람이 아래 문제를 줄이기 위해 만든
도구입니다.

- 최근 세션이 어디에 있는지 헷갈림
- 어떤 세션을 다시 열어야 하는지 매번 확인해야 함
- 에이전트 간 handoff를 매번 수작업으로 정리해야 함
- iTerm 탭과 세션 문맥이 따로 놀아서 흐름이 끊김

`asm`은 원본 세션 파일을 수정하지 않고, 별도의 로컬 메타데이터만 저장합니다.

## 현재 범위

- macOS 전용
- 터미널 중심 워크플로우
- `fzf` 기반 picker
- iTerm2 연동은 선택 사항
- 로컬 세션 탐색 중심
- exact chunk 우선 + 메타데이터 하이브리드 검색

## 주요 기능

- 여러 AI CLI 세션을 한 목록으로 통합
- `asm <query>` 형태의 빠른 진입 검색
- pin / alias / tag / tab / last-opened 메타데이터 관리
- overview / transcript / commands preview
- 에이전트 간 handoff / export bundle
- iTerm2 reopen / snapshot save / restore / clone
- Jira 내 이슈 조회 / 보기 / 열기
- smoke-test / self-test 기반 회귀 검증

## 지원 에이전트

현재 지원:

- Codex
- Claude Code
- OpenCode
- Cursor Agent
- Gemini

플랫폼/환경 의존 기능:

- iTerm2 창/탭 복원
- 현재 열려 있는 iTerm2 세션 import

## 요구 사항

필수:

- macOS
- `zsh` 또는 `bash`
- `fzf`
- `jq`
- `sqlite3`
- `python3`
- `rg`

선택:

- `pbcopy`
- `osascript`
- iTerm2
- `qmd` (의미 기반 재랭킹 보강용)

환경 점검:

```sh
./bin/asm doctor
```

## 설치

### GitHub Releases에서 설치

```sh
curl -L https://github.com/jalnanco/asm-cli/releases/latest/download/asm-cli-0.1.0.tar.gz -o asm-cli.tar.gz
tar -xzf asm-cli.tar.gz
cd asm-cli-0.1.0
./install.sh
```

### 작업 트리에서 설치

```sh
make install
```

설치 결과:

- `asm` → `~/.local/bin/asm`
- 런타임 헬퍼 → `~/.local/libexec/asm-cli`
- 문서 → `~/.local/share/doc/asm-cli`

그 다음 shell integration:

```sh
eval "$(asm init zsh)"
```

rc 파일에 영구 반영:

```sh
asm setup
```

### 릴리즈 번들에서 설치

압축을 푼 뒤:

```sh
./install.sh
```

설치 경로를 바꾸고 싶으면:

```sh
PREFIX=/custom/prefix ./install.sh
```

## 설정 체크리스트

설치 직후 실제로 쓸 수 있는 상태까지 가는 가장 빠른 순서입니다.

1. `asm`이 `PATH`에 잡혀 있는지 확인
2. shell integration 활성화
3. `asm doctor` 실행
4. 필요하면 Jira 설정
5. 자주 여는 조합이 있으면 `asm.json` preset 준비

예시:

```sh
export PATH="$HOME/.local/bin:$PATH"
eval "$(asm init zsh)"
asm doctor
```

영구 반영이 필요하면 rc 파일에 추가:

```sh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
echo 'eval "$(asm init zsh)"' >> ~/.zshrc
```

Bash 사용 시:

```sh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
echo 'eval "$(asm init bash)"' >> ~/.bashrc
```

Fish 사용 시:

```sh
asm init fish | source
```

## 빠른 시작

```sh
asm doctor
asm
asm list
asm "incident handoff"
asm inspect 2
asm resume 2
```

기본 흐름:

1. `asm` 또는 `asm <query>` 실행
2. picker에서 세션 선택
3. preview로 문맥 확인
4. `enter`로 resume, 또는 inspect / handoff / clone 실행

## 핵심 개념

### Session Ref

각 세션은 아래처럼 안정적인 ref를 가집니다.

```text
claude:11111111-1111-4111-8111-111111111111
codex:019d470b-a125-7533-ad4e-dc1d1219a9df
```

대부분의 명령은 다음 둘 중 하나를 받을 수 있습니다.

- 전체 ref
- `asm list` 이후 보이는 행 번호

### Picker

`asm`을 실행하면 interactive picker가 열립니다.

주요 키:

- `enter`: 선택 세션 즉시 resume
- `v`: revive 액션 메뉴
- `/`: 검색 모드 진입
- `esc`: 검색 모드 종료 또는 picker 닫기
- `1 / 2 / 3`: preview 모드 전환
- `p`: pin 토글
- `t`: tag 토글
- `a`: alias 수정
- `m / n / u`: 탭 이동 / 새 탭 / 탭에서 제거
- `h`: handoff 대상 선택
- `l`: 마지막 handoff 대상 재사용
- `i`: inspect
- `r`: 세션 목록 새로고침

검색 모드에서는 액션 키보다 문자 입력이 우선합니다.

### 검색

검색 진입은 두 가지입니다.

- `asm <query>`: 초기 랭킹된 결과로 picker 열기
- picker 안에서 `/`: interactive search mode

검색은 다음 순서로 강하게 반응합니다.

- exact chunk match
- alias
- title
- tags
- project / cwd
- session ref

`qmd`가 있으면 의미 기반 결과를 추가 신호로 사용합니다.

## 설정 예시

### 최소 일상 설정

```sh
export PATH="$HOME/.local/bin:$PATH"
eval "$(asm init zsh)"
```

### 선택 사항: `qmd`

`qmd`가 설치되어 있으면 `asm`은 로컬 메타데이터 검색 위에 의미 기반
재랭킹 신호를 추가로 사용합니다. `PATH`에만 잡혀 있으면 별도 설정은
필요 없습니다.

관련 명령:

```sh
asm index init
asm index update
asm index status
```

### Jira 설정

```sh
asm jira config set \
  --site your-site.atlassian.net \
  --email you@example.com \
  --token <api-token> \
  --max-issues 20
```

저장된 설정 확인:

```sh
asm jira config show
```

### `asm.json` Preset 파일

자주 여는 세션 조합이 있으면 프로젝트 루트에 `asm.json`을 둘 수 있습니다.

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

사용:

```sh
asm open --list
asm open triage
```

## 명령어 가이드

### 찾기 / 보기

```sh
asm
asm list
asm list -a
asm query "handoff timeout"
asm inspect 3
asm inspect claude:11111111-1111-4111-8111-111111111111
```

### 이어서 작업

```sh
asm resume 3
asm clone 3
asm revive 3
```

`asm clone` 후 새 세션을 감지할 수 있으면 로컬에 parent/child 관계를
기록합니다. picker와 inspect 화면에서는 child 쪽에 `fork<-...`, parent
쪽에 `forks:N`이 표시됩니다.

수동으로 관계를 남길 수도 있습니다.

```sh
asm relate codex:child-session --parent claude:parent-session
```

### 정리

```sh
asm pin 3
asm alias 3 "search ranking follow-up"
```

탭과 태그는 picker 안에서 다루는 경우가 가장 편하고, `pin` / `alias`는
직접 명령으로도 가능합니다.

### Handoff / Export

```sh
asm handoff --to claude 3
asm absorb --to codex 3
asm export --to portable 3
asm inspect --bundle 3
```

handoff 모드:

- `light`: 메타데이터 + 최근 excerpt
- `deep`: light + 최근 turn / decision / next action / file hint

### 점검 / 유지보수

```sh
asm doctor
asm status --compact
asm index status
asm smoke-test
asm self-test
```

## Jira 연동

한 번만 설정:

```sh
asm jira config set \
  --site your-site.atlassian.net \
  --email you@example.com \
  --token <api-token>
```

그 다음:

```sh
asm jira mine
asm jira view 2
asm jira open ASM-101
```

preview에서도 alias / title / tag / 최근 문맥에서 Jira key가 감지되면
바로 보여줍니다.

## Preset

`asm open`은 현재 디렉터리와 상위 디렉터리에서 `asm.json`을 찾습니다.

예시:

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

관련 명령:

```sh
asm open --list
asm open review
asm open --print review
```

## 개발

권장 검증:

```sh
make smoke
make test
make package
zsh -n bin/asm
bash -n scripts/install.sh scripts/build-release.sh
python3 -m py_compile bin/asm_runtime.py bin/asm_store.py bin/asm_inspect.py tests/asm_selftest.py
```

설명:

- `make smoke`: 빠른 회귀 테스트
- `make test`: 격리된 self-test 전체 실행
- self-test는 iTerm2 / AppleScript 흐름을 stub 처리해서 live session 없이도
  안정적으로 돌 수 있게 되어 있음

더 자세한 QA 커버리지는 [docs/qa-matrix.md](docs/qa-matrix.md) 참고.

## 릴리즈 플로우

```sh
make smoke
make test
make package
git tag "v$(cat VERSION)"
git push origin "v$(cat VERSION)"
```

release workflow는 다음을 보장합니다.

- tag와 `VERSION` 값 일치 확인
- portable tarball 생성
- archive + SHA256 checksum을 GitHub Releases에 업로드

## 개인정보 / 보안

`asm`은 정상 동작에 원격 서비스가 필요하지 않습니다.

자체 메타데이터는 주로 아래에 저장됩니다.

- `~/.local/share/asm/meta.sqlite`
- `~/.local/share/asm/session-cache.json`
- `~/.local/share/asm/exports`
- `~/.local/share/asm/iterm-layouts`

스크린샷, 로그, export bundle을 외부에 공유할 때는 prompt, 경로, 저장소 이름,
이슈 키, 최근 대화가 포함되어 있지 않은지 확인해야 합니다.

## 설계 원칙

- 원본 세션 파일은 수정하지 않음
- 도구별 메타데이터는 `asm` 저장소에 별도 보관
- picker는 터미널 상호작용에 최적화
- 비대화형 출력에서는 ANSI 색상을 자동으로 줄임

## 라이선스

MIT License. 자세한 내용은 [LICENSE](LICENSE) 참고.

## Attribution

이 프로젝트는 MIT 라이선스인
[`subinium/agf`](https://github.com/subinium/agf)에서 영감을 받았습니다.

자세한 고지는 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 참고.

## 면책

이 프로젝트는 Anthropic, OpenAI, Google, Anysphere 등 지원 대상 도구
벤더와 공식적으로 관련이 없습니다.
