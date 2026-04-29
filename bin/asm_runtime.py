#!/usr/bin/env python3
import json
import os
import re
import shlex
import shutil
import signal
import socket
import stat
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def clean_text(text: str) -> str:
    return " ".join((text or "").replace("\\n", " ").replace("\\t", " ").split())


def truncate_text(text: str, max_len: int) -> str:
    text = clean_text(text)
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def color(code: str, text: str) -> str:
    if not color_enabled():
        return text
    return f"\033[{code}m{text}\033[0m"


def color_enabled() -> bool:
    if os.environ.get("ASM_FORCE_COLOR"):
        return True
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("CLICOLOR_FORCE", "0") not in ("", "0"):
        return True
    return sys.stdout.isatty() and os.environ.get("TERM", "") != "dumb"


def compact_timestamp(now_epoch: int, epoch: int) -> str:
    current = datetime.fromtimestamp(now_epoch).astimezone()
    target = datetime.fromtimestamp(int(epoch)).astimezone()
    if current.date() == target.date():
        return target.strftime("%H:%M")
    if current.year == target.year:
        return target.strftime("%m-%d %H:%M")
    return target.strftime("%Y-%m-%d")


def short_relative_time(now_epoch: int, epoch: int) -> str:
    diff = max(0, int(now_epoch) - int(epoch))
    if diff < 60:
        return f"{diff}s"
    if diff < 3600:
        return f"{diff // 60}m"
    if diff < 86400:
        return f"{diff // 3600}h"
    if diff < 2592000:
        return f"{diff // 86400}d"
    return f"{diff // 2592000}mo"


def flex_timestamp(now_epoch: int, epoch: int, width: int) -> str:
    if width >= 150:
        return f"{short_relative_time(now_epoch, epoch)} {compact_timestamp(now_epoch, epoch)}"
    if width >= 110:
        return compact_timestamp(now_epoch, epoch)
    return short_relative_time(now_epoch, epoch)


def agent_label(agent: str) -> str:
    return {
        "codex": "CDX",
        "claude": "CLD",
        "opencode": "OPN",
        "cursor-agent": "CUR",
        "gemini": "GEM",
    }.get(agent, agent.upper())


def agent_glyph(agent: str) -> str:
    return {
        "codex": "X",
        "claude": "C",
        "opencode": "O",
        "cursor-agent": "U",
        "gemini": "G",
    }.get(agent, "?")


def agent_badge(agent: str, label: str) -> str:
    return {
        "codex": color("38;5;75", label),
        "claude": color("38;5;179", label),
        "opencode": color("38;5;71", label),
        "cursor-agent": color("38;5;110", label),
        "gemini": color("38;5;141", label),
    }.get(agent, color("38;5;245", label))


def pin_badge(text: str) -> str:
    return color("1;38;5;203", text) if text.strip() == "PIN" else color("38;5;240", text)


def muted(text: str) -> str:
    return color("38;5;244", text)


def search_result_meta(text: str) -> str:
    return color("38;5;117", text)


def search_result_field(text: str) -> str:
    return color("1;38;5;229", text)


def read_git_info(dir_path: str) -> tuple[str, int]:
    if not dir_path:
        return "", 0

    path = Path(dir_path)
    if not path.is_dir():
        path = path.parent
    if not path.is_dir():
        return "", 0

    git_dir = path / ".git"
    try:
        stat_result = git_dir.lstat()
    except OSError:
        return "", 0

    if stat.S_ISDIR(stat_result.st_mode):
        return read_git_branch(git_dir / "HEAD"), 0
    return read_git_branch_from_worktree_file(git_dir), 1


def read_git_branch(head_path: Path) -> str:
    try:
        line = head_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if line.startswith("ref: refs/heads/"):
        return line.removeprefix("ref: refs/heads/")
    return line[:8] if len(line) >= 8 else ""


def read_git_branch_from_worktree_file(git_file_path: Path) -> str:
    try:
        line = git_file_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not line.startswith("gitdir: "):
        return ""
    return read_git_branch(Path(line.removeprefix("gitdir: ")) / "HEAD")


def git_label(branch: str, is_worktree: int) -> str:
    if not branch:
        return ""
    return f"wt:{branch}" if int(is_worktree or 0) == 1 else branch


def project_display(row: dict) -> str:
    cwd = row.get("cwd", "")
    if not cwd or cwd == "/":
        project = "/"
    elif cwd == os.path.expanduser("~"):
        project = "~"
    else:
        project = Path(cwd).name
    branch = git_label(str(row.get("git_branch", "") or ""), int(row.get("git_is_worktree", 0) or 0))
    return f"{project}@{branch}" if branch else project


def searchable_text_for_row(row: dict) -> str:
    parts = [
        str(row.get("alias", "") or ""),
        str(row.get("title", "") or ""),
        str(row.get("tags_text", "") or ""),
        project_display(row),
        str(row.get("cwd", "") or ""),
        str(row.get("ref", "") or ""),
        str(row.get("parent_ref", "") or ""),
        " ".join(str(ref) for ref in row.get("child_refs", []) if ref),
        str(row.get("agent", "") or ""),
    ]
    return search_normalize(" ".join(part for part in parts if part))


def terminal_columns() -> int:
    for key in ("ASM_RENDER_COLUMNS", "FZF_COLUMNS", "COLUMNS"):
        value = os.environ.get(key, "")
        if value.isdigit():
            return int(value)
    return shutil.get_terminal_size(fallback=(140, 40)).columns


def active_slot(row: dict) -> str:
    if int(row.get("is_active", 0) or 0) != 1:
        return "-"
    return f"{row.get('active_window', 0)}:{row.get('active_tab', 0)}"


def active_badge(row: dict, width: int = 5) -> str:
    text = f"{active_slot(row):<{width}}"
    if int(row.get("is_active", 0) or 0) == 1:
        return color("1;38;5;114", text)
    return muted(text)


def compact_agent_cluster(row: dict) -> str:
    agent = str(row.get("agent", "") or "")
    glyph = agent_badge(agent, agent_glyph(agent))
    if int(row.get("pinned", 0) or 0) == 1:
        return f"{glyph}{color('1;38;5;203', '*')}"
    return glyph


def active_marker(row: dict) -> str:
    slot = active_slot(row)
    if slot == "-":
        return ""
    return color("1;38;5;114", f"@{slot}")


def short_ref(ref: str) -> str:
    agent, _, session_id = ref.partition(":")
    if not agent or not session_id:
        return ref[:18]
    return f"{agent}:{session_id[:8]}"


def relation_marker(row: dict) -> str:
    parent_ref = str(row.get("parent_ref", "") or "")
    child_count = int(row.get("child_count", 0) or 0)
    if parent_ref and child_count:
        return color("38;5;179", f"fork<-{short_ref(parent_ref)} +{child_count}")
    if parent_ref:
        return color("38;5;179", f"fork<-{short_ref(parent_ref)}")
    if child_count:
        return color("38;5;179", f"forks:{child_count}")
    return ""


def format_claude_usage_text(row: dict) -> str:
    usage = str(row.get("claude_usage_text", "") or "")
    if usage:
        return usage
    input_tokens = int(row.get("claude_input_tokens", 0) or 0)
    output_tokens = int(row.get("claude_output_tokens", 0) or 0)
    total_cost = float(row.get("claude_total_cost", 0.0) or 0.0)
    if input_tokens <= 0 and output_tokens <= 0 and total_cost <= 0:
        return ""
    return f"{format_tokens(input_tokens)} in / {format_tokens(output_tokens)} out / ~${total_cost:.2f}"


def format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


SEARCH_TOKEN_RE = re.compile(r"[\w./:-]+", re.UNICODE)


def search_normalize(text: str) -> str:
    return clean_text(text).lower()


def search_tokens(text: str) -> list[str]:
    normalized = search_normalize(text)
    tokens = [token for token in SEARCH_TOKEN_RE.findall(normalized) if len(token) > 1]
    if tokens:
        return tokens
    return [normalized] if normalized else []


def token_match_strength(query_token: str, field_tokens: list[str]) -> int:
    best = 0
    for token in field_tokens:
        if token == query_token:
            return 3
        if token.startswith(query_token):
            best = max(best, 2)
            continue
        if query_token in token:
            best = max(best, 1)
    return best


def score_search_field(text: str, query_norm: str, query_terms: list[str], weight: int) -> int:
    normalized = search_normalize(text)
    if not normalized:
        return 0

    field_tokens = search_tokens(normalized)
    score = 0
    matched_terms = 0

    if normalized == query_norm:
        score += weight * 100
    elif query_norm and normalized.startswith(query_norm):
        score += weight * 60
    elif query_norm and query_norm in normalized:
        score += weight * 35

    for term in query_terms:
        strength = token_match_strength(term, field_tokens)
        if strength == 3:
            score += weight * 18
            matched_terms += 1
        elif strength == 2:
            score += weight * 12
            matched_terms += 1
        elif strength == 1:
            score += weight * 6
            matched_terms += 1

    if query_terms and matched_terms == len(query_terms):
        score += weight * 22
    elif matched_terms > 0:
        score += matched_terms * weight * 3

    return score


def score_search_row(row: dict, query_norm: str, query_terms: list[str], qmd_rank: dict[str, int]) -> int:
    project = project_display(row)
    fields: list[tuple[str, int]] = [
        (str(row.get("alias", "") or ""), 8),
        (str(row.get("title", "") or ""), 7),
        (str(row.get("tags_text", "") or ""), 6),
        (project, 5),
        (str(row.get("cwd", "") or ""), 4),
        (str(row.get("ref", "") or ""), 3),
        (str(row.get("agent", "") or ""), 1),
    ]

    score = sum(score_search_field(text, query_norm, query_terms, weight) for text, weight in fields)

    combined = search_normalize(" ".join(text for text, _ in fields if text))
    combined_tokens = search_tokens(combined)
    matched_terms = sum(1 for term in query_terms if token_match_strength(term, combined_tokens) > 0)

    if query_norm and combined.startswith(query_norm):
        score += 45
    elif query_norm and query_norm in combined:
        score += 28

    if query_terms and matched_terms == len(query_terms):
        score += 36
    elif matched_terms > 0:
        score += matched_terms * 5

    ref = str(row.get("ref", "") or "")
    if ref in qmd_rank:
        score += max(0, 180 - qmd_rank[ref] * 4)

    return score


def field_matches_query(text: str, query_norm: str, query_terms: list[str]) -> bool:
    return score_search_field(text, query_norm, query_terms, 1) > 0


def rank_rows_for_query(rows: list[dict], query: str, qmd_rank: dict[str, int] | None = None) -> list[dict]:
    query_norm = search_normalize(query)
    if not query_norm:
        return rows

    rank_lookup = qmd_rank or {}
    query_terms = search_tokens(query_norm)
    ranked: list[dict] = []

    for index, row in enumerate(rows):
        scored = dict(row)
        scored["_input_index"] = index
        scored["search_score"] = score_search_row(scored, query_norm, query_terms, rank_lookup)
        if int(scored["search_score"]) > 0:
            ranked.append(scored)

    ranked.sort(
        key=lambda row: (
            -int(row.get("search_score", 0) or 0),
            int(row.get("_input_index", 0) or 0),
            -int(row.get("pinned", 0) or 0),
            -int(row.get("updated_epoch", 0) or 0),
            str(row.get("agent", "") or ""),
            str(row.get("id", "") or ""),
        )
    )

    for row in ranked:
        row.pop("_input_index", None)
    return ranked


def cmd_rank_query(cache_file: str, query: str, qmd_refs_json: str = "[]") -> int:
    rows = json.loads(Path(cache_file).read_text(encoding="utf-8"))
    ranked_refs = json.loads(qmd_refs_json or "[]")
    qmd_rank = {str(ref): index for index, ref in enumerate(ranked_refs) if ref}
    rows = rank_rows_for_query(rows, query, qmd_rank)
    print(json.dumps(rows, ensure_ascii=False))
    return 0


def load_claude_usage(session_id: str, cwd: str) -> dict[str, int | float | str] | None:
    if not session_id or not cwd:
        return None
    encoded = cwd.replace(os.path.sep, "-")
    path = Path.home() / ".claude" / "projects" / encoded / f"{session_id}.jsonl"
    if not path.is_file():
        fallback_root = Path.home() / ".claude" / "projects"
        candidates = list(fallback_root.rglob(f"{session_id}.jsonl")) if fallback_root.exists() else []
        if candidates:
            path = candidates[0]
    if not path.is_file():
        return None

    input_tokens = 0
    output_tokens = 0
    cache_read = 0
    cache_write = 0

    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if '"usage"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if row.get("type") != "assistant":
                    continue
                usage = ((row.get("message") or {}).get("usage") or {})
                input_tokens += int(usage.get("input_tokens", 0) or 0)
                output_tokens += int(usage.get("output_tokens", 0) or 0)
                cache_read += int(usage.get("cache_read_input_tokens", 0) or 0)
                cache_write += int(usage.get("cache_creation_input_tokens", 0) or 0)
    except OSError:
        return None

    if input_tokens <= 0 and output_tokens <= 0 and cache_read <= 0 and cache_write <= 0:
        return None

    total_cost = (
        float(input_tokens) / 1_000_000 * 15.0
        + float(output_tokens) / 1_000_000 * 75.0
        + float(cache_read) / 1_000_000 * 1.5
        + float(cache_write) / 1_000_000 * 18.75
    )
    usage_text = f"{format_tokens(input_tokens)} in / {format_tokens(output_tokens)} out / ~${total_cost:.2f}"
    return {
        "claude_input_tokens": input_tokens,
        "claude_output_tokens": output_tokens,
        "claude_cache_read_tokens": cache_read,
        "claude_cache_write_tokens": cache_write,
        "claude_total_cost": total_cost,
        "claude_usage_text": usage_text,
    }


def cmd_render_lines(cache_file: str, active_tab: str, now_epoch: int, query: str = "") -> int:
    path = Path(cache_file)
    if not path.exists():
        return 0
    rows = json.loads(path.read_text(encoding="utf-8"))
    rows = rank_rows_for_query(rows, query) if query else rows
    query_norm = search_normalize(query)
    query_terms = search_tokens(query_norm) if query_norm else []
    width = terminal_columns()
    for row in rows:
        tab_name = row.get("tab_name", "")
        if active_tab != "All" and tab_name != active_tab:
            continue
        updated = flex_timestamp(now_epoch, int(row.get("updated_epoch", 0) or 0), width)
        title = row.get("alias") or row.get("title") or str(row.get("id", ""))[:12]
        tags = row.get("tags_text", "")
        if tags:
            title = f"{title}  {tags}"
        project_source = project_display(row)
        project = truncate_text(project_source, 20 if width < 120 else 28)
        title_text = truncate_text(title, 48 if width < 120 else 88)
        updated_part = muted(updated)
        project_part = muted(project)
        title_part = title_text
        if query_terms:
            updated_part = search_result_meta(updated)
            if field_matches_query(project_source, query_norm, query_terms):
                project_part = search_result_field(project)
            if field_matches_query(title, query_norm, query_terms):
                title_part = search_result_field(title_text)
        parts = [
            compact_agent_cluster(row),
            active_marker(row),
            relation_marker(row),
            updated_part,
            project_part,
            title_part,
        ]
        rendered = " ".join(part for part in parts if part)
        print(
            "\t".join(
                [
                    row.get("ref", ""),
                    searchable_text_for_row(row),
                    rendered,
                ]
            )
        )
    return 0


def _date_group(now_epoch: int, epoch: int) -> str:
    now = datetime.fromtimestamp(now_epoch).astimezone()
    target = datetime.fromtimestamp(epoch).astimezone()
    if now.date() == target.date():
        return "오늘"
    from datetime import timedelta
    if (now.date() - target.date()).days == 1:
        return "어제"
    now_monday = now.date() - timedelta(days=now.weekday())
    if target.date() >= now_monday:
        return "이번 주"
    return "이전"


def cmd_render_list(cache_file: str, now_epoch: int, limit: int) -> int:
    path = Path(cache_file)
    if not path.exists():
        return 0
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not rows:
        print("No sessions.")
        return 0

    total = len(rows)
    display_rows = rows if limit <= 0 else rows[:limit]
    current_group = ""

    for idx, row in enumerate(display_rows, start=1):
        epoch = int(row.get("updated_epoch", 0) or 0)
        group = _date_group(now_epoch, epoch)
        if group != current_group:
            current_group = group
            header = f"── {group} "
            print(color("38;5;245", header + "─" * max(0, 60 - len(header))))

        agent = row.get("agent", "")
        pinned = "PIN" if row.get("pinned", 0) == 1 else "   "
        updated = compact_timestamp(now_epoch, epoch)
        cwd = row.get("cwd", "")
        if not cwd or cwd == "/":
            project = "/"
        elif cwd == os.path.expanduser("~"):
            project = "~"
        else:
            project = Path(cwd).name
        title = row.get("alias") or row.get("title") or str(row.get("id", ""))[:12]
        tags = row.get("tags_text", "")
        if tags:
            title = f"{title}  {tags}"
        relation = relation_marker(row)
        if relation:
            title = f"{relation}  {title}"
        print(
            f"{muted(f'{idx:>3}.')}  {pin_badge(f'{pinned:<3}')}  {agent_badge(agent, f'{agent_label(agent):<3}')}  "
            f"{active_badge(row)}  {muted(f'{updated:<11}')}  {muted(f'{truncate_text(project_display(row), 28):<28}')}  "
            f"{truncate_text(title, 80)}"
        )

    if limit > 0 and total > limit:
        remaining = total - limit
        print(muted(f"\n  ... {remaining} more (asm list -a)"))

    if display_rows:
        print(muted("\n  open with: asm resume N | asm inspect N | asm handoff N"))

    return 0


def cmd_codex_file_path(home: str, session_id: str, cache_path: str) -> int:
    home_path = Path(home)
    cache = Path(cache_path)
    index = load_codex_file_index(home_path, cache)

    path = index.get(session_id, "")
    if path:
        print(path)
    return 0


def build_codex_file_index(sessions_root: Path, cache_path: Path) -> dict[str, str]:
    mapping = {}
    if sessions_root.exists():
        for path in sessions_root.rglob("*.jsonl"):
            match = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$", path.name)
            if match:
                mapping[match.group(1)] = str(path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"created_at": int(time.time()), "index": mapping}), encoding="utf-8")
    return mapping


def load_codex_file_index(home_path: Path, cache_path: Path, ttl_seconds: int = 300) -> dict[str, str]:
    sessions_root = home_path / ".codex" / "sessions"
    try:
        if cache_path.exists():
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            if int(time.time()) - int(payload.get("created_at", 0)) < ttl_seconds:
                return payload.get("index", {})
        return build_codex_file_index(sessions_root, cache_path)
    except Exception:
        return build_codex_file_index(sessions_root, cache_path)


def is_codex_bootstrap_text(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "AGENTS.md instructions",
            "<INSTRUCTIONS>",
            "<environment_context>",
            "<permissions instructions>",
            "<collaboration_mode>",
        )
    )


def parse_codex_session_file(path: Path) -> tuple[str, str]:
    cwd = ""
    title = ""
    fallback_title = ""
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                row_type = row.get("type")
                payload = row.get("payload") or {}
                if not cwd and row_type == "session_meta":
                    cwd = payload.get("cwd", "") or ""
                if (
                    not title
                    and row_type == "response_item"
                    and payload.get("type") == "message"
                    and payload.get("role") == "user"
                ):
                    content = payload.get("content") or []
                    if isinstance(content, list):
                        parts = [
                            part.get("text", "")
                            for part in content
                            if isinstance(part, dict) and part.get("type") in ("input_text", "text")
                        ]
                        text = clean_text(" ".join(parts))
                        if text and not fallback_title:
                            fallback_title = text
                        if text and not is_codex_bootstrap_text(text):
                            title = text
                if cwd and title:
                    break
    except Exception:
        return "", ""
    if not title:
        title = fallback_title
    return cwd, title


def cmd_list_codex(home: str, limit: int, cache_path: str) -> int:
    home_path = Path(home)
    index_file = home_path / ".codex" / "session_index.jsonl"
    sessions_root = home_path / ".codex" / "sessions"
    if not sessions_root.exists() and not index_file.exists():
        return 0

    file_index = load_codex_file_index(home_path, Path(cache_path))
    indexed_meta: dict[str, dict[str, int | str]] = {}

    if index_file.exists():
        with index_file.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                session_id = row.get("id")
                if not session_id:
                    continue
                updated_at = row.get("updated_at", "")
                updated_epoch = 0
                if updated_at:
                    try:
                        updated_epoch = int(datetime.fromisoformat(updated_at.replace("Z", "+00:00")).timestamp())
                    except Exception:
                        updated_epoch = 0
                previous = indexed_meta.get(session_id)
                if previous and int(previous.get("updated_epoch", 0)) > updated_epoch:
                    continue
                indexed_meta[session_id] = {
                    "title": clean_text(row.get("thread_name", "")),
                    "updated_at": updated_at,
                    "updated_epoch": updated_epoch,
                }

    candidates = []
    for session_id, path_str in file_index.items():
        path = Path(path_str)
        if not path.exists():
            continue
        try:
            file_epoch = int(path.stat().st_mtime)
        except OSError:
            continue
        index_epoch = int((indexed_meta.get(session_id) or {}).get("updated_epoch", 0) or 0)
        candidates.append((max(file_epoch, index_epoch), session_id, path))

    for updated_epoch, session_id, path in sorted(candidates, reverse=True)[:limit]:
        meta = indexed_meta.get(session_id, {})
        cwd, parsed_title = parse_codex_session_file(path)
        title = str(meta.get("title") or parsed_title)
        updated_display = str(
            meta.get("updated_at")
            or datetime.fromtimestamp(updated_epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        print(
            json.dumps(
                {
                    "agent": "codex",
                    "id": session_id,
                    "title": title,
                    "cwd": cwd,
                    "updated_epoch": updated_epoch,
                    "updated_display": updated_display,
                },
                ensure_ascii=False,
            )
        )
    return 0


_CMD_NAME_RE = re.compile(r"<command-name>(.*?)</command-name>", re.DOTALL)
_CMD_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
_HANDOFF_TITLE_RE = re.compile(r"^- Known title:\s*(.+)", re.MULTILINE)
_HANDOFF_SOURCE_RE = re.compile(r"^- Source agent:\s*(\S+)", re.MULTILINE)

_SKIP_COMMANDS = frozenset({"/clear"})


def _parse_handoff_title(text: str) -> str:
    title_m = _HANDOFF_TITLE_RE.search(text)
    source_m = _HANDOFF_SOURCE_RE.search(text)
    known = clean_text(title_m.group(1)) if title_m else ""
    source = source_m.group(1) if source_m else ""
    if known in ("", "-", "# Session Handoff"):
        known = ""
    parts = [p for p in [known, f"from {source}" if source else ""] if p]
    return "handoff: " + " ".join(parts) if parts else "handoff"


def extract_claude_title(text: str) -> str | None:
    """Parse command tags / handoff boilerplate from a Claude user message.

    Returns a cleaned title string, or None to signal "skip this message,
    keep looking at subsequent user messages".
    """
    text = text.strip()
    if not text:
        return None

    if text.startswith("# Session Handoff"):
        return _parse_handoff_title(text)

    name_m = _CMD_NAME_RE.search(text)
    if not name_m:
        return text  # normal message

    cmd_name = clean_text(name_m.group(1))
    args_m = _CMD_ARGS_RE.search(text)
    args_text = clean_text(args_m.group(1)) if args_m else ""

    if args_text:
        return args_text
    if cmd_name in _SKIP_COMMANDS:
        return None
    return cmd_name


def cmd_list_claude(home: str, limit: int, cache_path: str) -> int:
    root = Path(home) / ".claude" / "projects"
    cache = Path(cache_path)
    files = sorted(
        [p for p in root.rglob("*.jsonl") if "/subagents/" not in str(p)],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    try:
        payload = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    except Exception:
        payload = {}

    next_cache = {}
    count = 0
    for path in files:
        stat = path.stat()
        key = str(path)
        cached = payload.get(key)
        record = None
        if cached and cached.get("mtime") == stat.st_mtime and cached.get("size") == stat.st_size:
            record = cached.get("record")
        else:
            session_id = ""
            cwd = ""
            title = ""
            try:
                with path.open(encoding="utf-8") as fh:
                    for line in fh:
                        obj = json.loads(line)
                        if not session_id and obj.get("sessionId"):
                            session_id = obj.get("sessionId", "")
                        if not cwd and obj.get("cwd"):
                            cwd = obj.get("cwd", "")
                        if not title and obj.get("type") == "user" and not obj.get("isMeta", False):
                            msg = (obj.get("message") or {}).get("content")
                            if isinstance(msg, str):
                                text = msg
                            elif isinstance(msg, list):
                                text = " ".join(
                                    part.get("text", "")
                                    for part in msg
                                    if isinstance(part, dict) and part.get("type") == "text"
                                )
                            else:
                                text = ""
                            if text:
                                parsed = extract_claude_title(text)
                                if parsed is not None:
                                    title = parsed
                        if session_id and cwd and title:
                            break
            except Exception:
                continue
            if not session_id:
                continue
            updated_epoch = int(stat.st_mtime)
            updated_display = datetime.fromtimestamp(updated_epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            record = {
                "agent": "claude",
                "id": session_id,
                "title": title,
                "cwd": cwd,
                "updated_epoch": updated_epoch,
                "updated_display": updated_display,
            }
        next_cache[key] = {"mtime": stat.st_mtime, "size": stat.st_size, "record": record}
        print(json.dumps(record, ensure_ascii=False))
        count += 1
        if count >= limit:
            break

    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(next_cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return 0


def cmd_import_iterm_tabs(timeout_s: float) -> int:
    script = r'''
tell application "iTerm2"
  if (count of windows) = 0 then return ""
  tell current window
    set rowList to {}
    set tabIndex to 1
    repeat with t in tabs
      try
        set bodyText to contents of current session of t
      on error
        set bodyText to ""
      end try
      if (length of bodyText) > 3000 then
        set bodyText to text -3000 thru -1 of bodyText
      end if
      copy ((tabIndex as string) & "<<<ASMSEP>>>" & bodyText) to end of rowList
      set tabIndex to tabIndex + 1
    end repeat
    set oldTids to AppleScript's text item delimiters
    set AppleScript's text item delimiters to "<<<ASMROW>>>"
    set joinedText to rowList as text
    set AppleScript's text item delimiters to oldTids
    return joinedText
  end tell
end tell
'''
    patterns = [
        (re.compile(r'claude(?:\s+--[^\s]+)*\s+--resume\s+([0-9a-fA-F-]{36})'), 'claude:{}'),
        (re.compile(r'gemini --resume ([0-9a-fA-F-]{36})'), 'gemini:{}'),
        (re.compile(r'cursor-agent --resume ([0-9a-fA-F-]{36})'), 'cursor-agent:{}'),
        (re.compile(r'codex(?:\s+--[^\s]+)*\s+resume\s+(?!-)([A-Za-z0-9._:-]+)'), 'codex:{}'),
        (re.compile(r'opencode -s ([A-Za-z0-9._:-]+)'), 'opencode:{}'),
    ]
    try:
        proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired:
        return 0
    if proc.returncode != 0 or not proc.stdout:
        return 0
    for row in proc.stdout.split("<<<ASMROW>>>"):
        if "<<<ASMSEP>>>" not in row:
            continue
        idx, body = row.split("<<<ASMSEP>>>", 1)
        found = None
        for pattern, template in patterns:
            matches = pattern.findall(body)
            if matches:
                found = template.format(matches[-1])
        if found:
            print(f"iterm-{idx}\t{found}")
    return 0


def cmd_extract_current_window(source_path: str, target_path: str, snapshot_name: str) -> int:
    source = json.loads(Path(source_path).read_text(encoding="utf-8"))
    windows = [w for w in source.get("windows", []) if w.get("is_current_window") is True]
    if not windows:
        raise SystemExit(1)
    window = windows[0]
    payload = {
        "version": source.get("version", 1),
        "created_at": source.get("created_at", ""),
        "snapshot_name": snapshot_name,
        "host": source.get("host", ""),
        "window_count": 1,
        "session_count": sum(len(tab.get("sessions", [])) for tab in window.get("tabs", [])),
        "windows": [{**window, "window_order": 1, "is_current_window": True}],
    }
    Path(target_path).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return 0


_STATUS_AGENT_NAMES = frozenset({"codex", "claude", "opencode", "cursor-agent", "gemini"})

_STATUS_TRANSIENT_MARKERS = ("/.local/bin/asm", " asm ", " fzf", "/fzf")


def _parse_agent_from_command(command: str) -> tuple[str, int, list[str]]:
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    for index, token in enumerate(tokens):
        base = os.path.basename(token)
        if base in _STATUS_AGENT_NAMES:
            return base, index, tokens
        if base == "node" and index + 1 < len(tokens):
            next_base = os.path.basename(tokens[index + 1])
            if next_base in _STATUS_AGENT_NAMES:
                return next_base, index + 1, tokens
    return "", -1, tokens


def _session_id_from_tokens(agent: str, agent_index: int, tokens: list[str]) -> str:
    if agent_index < 0:
        return ""
    if agent == "codex":
        for i in range(agent_index + 1, len(tokens) - 1):
            if tokens[i] == "resume" and not tokens[i + 1].startswith("-"):
                return tokens[i + 1]
    elif agent in {"claude", "cursor-agent", "gemini"}:
        for i in range(agent_index + 1, len(tokens) - 1):
            if tokens[i] == "--resume" and not tokens[i + 1].startswith("-"):
                return tokens[i + 1]
    elif agent == "opencode":
        for i in range(agent_index + 1, len(tokens) - 1):
            if tokens[i] == "-s" and not tokens[i + 1].startswith("-"):
                return tokens[i + 1]
    return ""


def _is_transient(command: str) -> bool:
    cmd_lower = (command or "").strip().lower()
    return any(m in cmd_lower for m in _STATUS_TRANSIENT_MARKERS) if cmd_lower else False


def _ps_tty(tty: str) -> list[dict]:
    if not tty:
        return []
    tty_name = tty.replace("/dev/", "")
    try:
        proc = subprocess.run(
            ["ps", "-t", tty_name, "-o", "pid=,ppid=,stat=,command="],
            capture_output=True, text=True, timeout=1, check=False,
        )
    except subprocess.TimeoutExpired:
        return []
    procs = []
    for line in proc.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) >= 5 and parts[2].isdigit():
            parts = [parts[0], parts[1], parts[3], parts[4]]
        if len(parts) < 4:
            continue
        try:
            procs.append({"pid": int(parts[0]), "ppid": int(parts[1]), "stat": parts[2], "command": parts[3]})
        except ValueError:
            continue
    return procs


def _cwd_for_pid(pid: int) -> str:
    try:
        proc = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            capture_output=True, text=True, timeout=1, check=False,
        )
    except subprocess.TimeoutExpired:
        return ""
    for line in proc.stdout.splitlines():
        if line.startswith("n"):
            return line[1:]
    return ""


def _project_label(cwd: str) -> str:
    home = os.path.expanduser("~")
    if not cwd or cwd == "/":
        return "/"
    if cwd == home:
        return "~"
    return Path(cwd).name


def _claude_session_id_for_pid(pid: int) -> str:
    meta_path = Path.home() / ".claude" / "sessions" / f"{pid}.json"
    if not meta_path.is_file():
        return ""
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    return str(meta.get("sessionId", "") or "")


def _active_sessions(raw_scan: str, show_all: bool = False) -> list[dict]:
    sessions: list[dict] = []
    for line in raw_scan.strip().splitlines():
        if not line.startswith("S<<<ASMSEP>>>"):
            continue
        parts = line.split("<<<ASMSEP>>>")
        if len(parts) < 5:
            continue
        sessions.append({
            "window": int(parts[1]),
            "tab": int(parts[2]),
            "session_order": int(parts[3]),
            "tty": parts[4],
        })

    results: list[dict] = []
    for sess in sessions:
        tty = sess["tty"]
        if not tty:
            continue
        procs = _ps_tty(tty)
        if not procs:
            continue

        # Find shell and foreground process (direct child of shell preferred)
        shell_pid = 0
        candidate = None
        for p in procs:
            if re.match(r"^-(zsh|bash|fish)$", p["command"]):
                shell_pid = p["pid"]
                break
        if shell_pid:
            for p in procs:
                if p["ppid"] == shell_pid and "+" in p["stat"]:
                    candidate = p
                    break
        if not candidate:
            for p in procs:
                if "+" in p["stat"] and not re.match(r"^-(zsh|bash|fish)$", p["command"]):
                    candidate = p
                    break

        if not candidate:
            if not show_all:
                continue
            # Plain shell — include with --all
            cwd = _cwd_for_pid(shell_pid) if shell_pid else ""
            results.append({**sess, "agent": "", "session_id": "", "cwd": cwd, "title": ""})
            continue

        command = candidate["command"]
        if _is_transient(command):
            continue

        agent, agent_idx, tokens = _parse_agent_from_command(command)
        if not agent:
            if not show_all:
                continue
            cwd = _cwd_for_pid(candidate["pid"])
            results.append({**sess, "agent": "", "session_id": "", "cwd": cwd, "title": command})
            continue

        session_id = _session_id_from_tokens(agent, agent_idx, tokens)
        if not session_id and agent == "claude":
            session_id = _claude_session_id_for_pid(candidate["pid"])

        ref = f"{agent}:{session_id}" if session_id else ""
        cwd = _cwd_for_pid(candidate["pid"])
        git_branch, git_is_worktree = read_git_info(cwd)

        results.append(
            {
                **sess,
                "agent": agent,
                "session_id": session_id,
                "ref": ref,
                "cwd": cwd,
                "command": command,
                "git_branch": git_branch,
                "git_is_worktree": git_is_worktree,
            }
        )

    return results


def cmd_enrich_cache(cache_file: str, active_scan_file: str = "") -> int:
    path = Path(cache_file)
    if not path.exists():
        print("[]")
        return 0

    rows = json.loads(path.read_text(encoding="utf-8"))
    raw_scan = ""
    if active_scan_file:
        scan_path = Path(active_scan_file)
        if scan_path.exists():
            raw_scan = scan_path.read_text(encoding="utf-8")
    active_by_ref: dict[str, dict] = {}
    if raw_scan.strip():
        for row in _active_sessions(raw_scan):
            ref = str(row.get("ref", "") or "")
            if ref and ref not in active_by_ref:
                active_by_ref[ref] = row

    enriched = []
    for row in rows:
        record = dict(row)
        cwd = str(record.get("cwd", "") or "")
        git_branch, git_is_worktree = read_git_info(cwd)
        record["git_branch"] = git_branch
        record["git_is_worktree"] = git_is_worktree

        active = active_by_ref.get(str(record.get("ref", "") or ""))
        if active:
            record["is_active"] = 1
            record["active_window"] = int(active.get("window", 0) or 0)
            record["active_tab"] = int(active.get("tab", 0) or 0)
            record["active_session_order"] = int(active.get("session_order", 0) or 0)
            record["active_tty"] = str(active.get("tty", "") or "")
            record["active_command"] = str(active.get("command", "") or "")
        else:
            record["is_active"] = 0
            record["active_window"] = 0
            record["active_tab"] = 0
            record["active_session_order"] = 0
            record["active_tty"] = ""
            record["active_command"] = ""

        if str(record.get("agent", "")) == "claude":
            usage = load_claude_usage(str(record.get("id", "") or ""), cwd)
            if usage:
                record.update(usage)
            else:
                record["claude_usage_text"] = ""
        else:
            record["claude_usage_text"] = ""

        enriched.append(record)

    print(json.dumps(enriched, ensure_ascii=False))
    return 0


def cmd_status(
    raw_scan: str,
    cache_file: str,
    now_epoch: int,
    show_all: bool = False,
    output_mode: str = "human",
) -> int:
    """Show currently active agent sessions in iTerm tabs."""
    results = _active_sessions(raw_scan, show_all=show_all)

    if not results:
        if output_mode == "json":
            print("[]")
            return 0
        if output_mode == "compact":
            return 0
        print("No active agent sessions.")
        return 0

    cache_lookup: dict[str, dict] = {}
    try:
        rows = json.loads(Path(cache_file).read_text(encoding="utf-8"))
        for row in rows:
            ref = row.get("ref", "")
            if ref:
                cache_lookup[ref] = row
    except Exception:
        pass

    rendered: list[dict] = []
    for r in results:
        ref = str(r.get("ref", "") or "")
        cached = cache_lookup.get(ref, {})
        cwd = str(r.get("cwd", "") or cached.get("cwd", "") or "")
        title = str(cached.get("alias") or cached.get("title") or "")
        rendered.append(
            {
                **r,
                "cwd": cwd,
                "title": title,
            }
        )

    if output_mode == "json":
        print(json.dumps(rendered, ensure_ascii=False))
        return 0

    if output_mode == "compact":
        chunks = []
        for r in rendered:
            agent = str(r.get("agent", "") or "")
            label = agent_label(agent) if agent else "SH"
            chunks.append(f"{label}@{r['window']}:{r['tab']}")
        if chunks:
            print(" ".join(chunks))
        return 0

    for r in rendered:
        agent = str(r.get("agent", "") or "")
        badge = agent_badge(agent, f"{agent_label(agent):<3}") if agent else muted("---")
        project_text = project_display(r)
        project = muted(f"{truncate_text(project_text, 26):<26}")
        title = str(r.get("title", "") or "")
        session_id = str(r.get("session_id", "") or "")
        title_text = truncate_text(title, 60) if title else muted(session_id[:12] if session_id else "-")
        tab_str = f"{r['window']}:{r['tab']}"
        print(f"  {tab_str:<5}  {badge}  {project}  {title_text}")

    return 0


def main() -> int:
    cmd = sys.argv[1]
    if cmd == "render-lines":
        query = sys.argv[5] if len(sys.argv) > 5 else ""
        return cmd_render_lines(sys.argv[2], sys.argv[3], int(sys.argv[4]), query)
    if cmd == "render-list":
        return cmd_render_list(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]))
    if cmd == "rank-query":
        qmd_refs_json = sys.argv[4] if len(sys.argv) > 4 else "[]"
        return cmd_rank_query(sys.argv[2], sys.argv[3], qmd_refs_json)
    if cmd == "codex-file-path":
        return cmd_codex_file_path(sys.argv[2], sys.argv[3], sys.argv[4])
    if cmd == "list-codex":
        return cmd_list_codex(sys.argv[2], int(sys.argv[3]), sys.argv[4])
    if cmd == "list-claude":
        return cmd_list_claude(sys.argv[2], int(sys.argv[3]), sys.argv[4])
    if cmd == "import-iterm-tabs":
        return cmd_import_iterm_tabs(float(sys.argv[2]))
    if cmd == "extract-current-window":
        return cmd_extract_current_window(sys.argv[2], sys.argv[3], sys.argv[4])
    if cmd == "enrich-cache":
        active_scan_file = sys.argv[3] if len(sys.argv) > 3 else ""
        return cmd_enrich_cache(sys.argv[2], active_scan_file)
    if cmd == "status":
        show_all = "--all" in sys.argv
        output_mode = "human"
        if "--compact" in sys.argv:
            output_mode = "compact"
        if "--json" in sys.argv:
            output_mode = "json"
        return cmd_status(sys.argv[2], sys.argv[3], int(sys.argv[4]), show_all=show_all, output_mode=output_mode)
    raise SystemExit(2)


if __name__ == "__main__":
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except OSError:
            pass
        raise SystemExit(0)
