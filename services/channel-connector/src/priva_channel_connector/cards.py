"""Pure Feishu interactive-card (schema 2.0 / card-json-v2) renderer over a StreamState.

No ``lark_oapi`` import — this only builds the card *dict*; the transport serializes and
sends/patches it. Kept separate from ``sse.py`` (pure reducer) and ``lark_ws.py`` (I/O) to
preserve the seam split, and unit-testable on its own.

Schema 2.0: ``{schema:"2.0", config, body:{elements:[...]}}``. NOTE: 2.0 dropped the
``note`` element (Feishu rejects it: "unsupported tag note"), so the running footer is a
gray ``markdown`` element, not a ``note``. create + patch of this structure were validated
live against Feishu.

Design decisions (2026-07-16, user-confirmed):
  - NO header; text and tool steps render in MESSAGE ORDER (interleaved) off the
    ``StreamState.timeline`` — not all steps lumped at the end;
  - tool steps are grouped by contiguous run into a ``collapsible_panel`` that is ALWAYS
    folded (``expanded:false``). Feishu treats ``expanded`` as the initial state and won't
    re-collapse a patched panel, so panels are created folded and stay folded;
  - while running a gray "Thinking" footer signals activity, its dot count cycling 1→2→3
    (animated by the worker patching with an incrementing ``dots``); dropped once final;
  - an error is shown by a ⚠️ prefix element (no coloured header);
  - tool step rows = glyph + tool name + one-line input summary (D2).
"""

from __future__ import annotations

from .sse import StreamState, ToolStep

_BODY_MAX = 3000        # tail-cap a streamed text run so the card stays well under Feishu's 30KB
_ERR_MAX = 500          # head-cap the error prefix (kept intact, never clipped away)
_STEPS_MAX = 30         # keep only the most recent N step rows in a panel
# Status glyphs. Done is a GREEN check; error a red cross (Feishu markdown font colours).
_GLYPH = {
    "running": "⟳",
    "done": "<font color='green'>✔</font>",
    "error": "<font color='red'>✗</font>",
}

# --- agent-UI tool-run summary parity ---------------------------------------
# The folded panel header shows the SAME aggregated phrase the agent UI's collapsed tool
# run shows (web/user/src/utils/toolRunSummary.js `summarizeRun`): grouped counts joined by
# ", " in GROUP_ORDER, with +added/-removed line deltas appended after the file-op group.
# zh phrasing mirrors web/shared/locales/zh.json `toolCall.summary.*` (card language is zh,
# matching the rest of the card). Chinese has no case, so JS `lowercaseFirst` is a no-op here.
_GROUP_ORDER = ("edited", "wrote", "generated", "read", "search",
                "bash", "webFetch", "webSearch", "canvas", "other")
_GROUP_PHRASE = {
    "edited": "编辑了 {n} 个文件",
    "wrote": "写入了 {n} 个文件",
    "generated": "预览了 {n} 个文件",
    "read": "读取了 {n} 个文件",
    "search": "搜索了 {n} 个模式",
    "bash": "执行了 {n} 条 bash 命令",
    "webFetch": "抓取了 {n} 个 URL",
    "webSearch": "联网搜索了 {n} 次",
    "canvas": "在画布中执行了 {n} 个任务",
    "other": "执行了 {n} 个其他工具",
}
# FileCanvas / legacy generated-tool names → the "generated" (预览) group (generatedTool.js).
_GENERATED_TOOL_NAMES = {
    "mcp__priva_File__FileCanvas", "FileCanvas",
    "mcp__priva_generated__Generated", "Generated",
    "mcp__priva_File__FIleCanvas", "FIleCanvas",
}


def _count_lines(s) -> int:
    """Line count matching the web UI's ``countContentLines`` (toolRunSummary.js): strip a
    single trailing newline, then count the remaining lines. Empty/non-string → 0."""
    if not isinstance(s, str) or not s:
        return 0
    normalized = s[:-1] if s.endswith("\n") else s
    if not normalized:
        return 0
    return normalized.count("\n") + 1


def _line_delta(name: str, tool_input) -> tuple[int, int]:
    """``(added, removed)`` for a file-mutation tool, derived here (in the card layer) from
    the step's raw input — mirroring the web UI's fallback path (no structured patch):
    Write counts its ``content`` lines added; Edit counts ``new_string`` added and
    ``old_string`` removed. Other tools / missing input → (0, 0)."""
    if not isinstance(tool_input, dict):
        return (0, 0)
    if name == "Write":
        return (_count_lines(tool_input.get("content")), 0)
    if name == "Edit":
        return (_count_lines(tool_input.get("new_string")), _count_lines(tool_input.get("old_string")))
    return (0, 0)


def _group_of(name: str) -> str:
    """Non-file-mutation tool → its summary group (Edit/Write handled separately with the
    success gate). Mirrors the else-chain in `summarizeRun`; unknown tools → 'other'."""
    if name in _GENERATED_TOOL_NAMES:
        return "generated"
    if name in ("Grep", "Glob"):
        return "search"
    if name == "Bash":
        return "bash"
    if name == "Read":
        return "read"
    if name == "WebFetch":
        return "webFetch"
    if name == "WebSearch":
        return "webSearch"
    return "other"


def _run_summary(steps) -> str:
    """Aggregate a contiguous run of ToolSteps into the agent-UI summary phrase. Edit/Write
    are counted (and their line deltas summed) only once successful — ``status == 'done'`` —
    mirroring ``isSuccessfulFileMutation``; every other tool counts as soon as it appears.
    The delta (green +added / red −removed) attaches after the wrote-or-edited phrase, as in
    the web UI. Returns '' when nothing groups yet (e.g. a still-running Edit) so the caller
    can fall back to a plain step count."""
    counts = {k: 0 for k in _GROUP_ORDER}
    total_added = total_removed = 0
    for st in steps:
        if st.name in ("Edit", "Write"):
            if st.status != "done":          # successful file mutation only (agent-UI parity)
                continue
            counts["edited" if st.name == "Edit" else "wrote"] += 1
            added, removed = _line_delta(st.name, st.tool_input)
            total_added += added
            total_removed += removed
        else:
            counts[_group_of(st.name)] += 1

    last_file_key = "wrote" if counts["wrote"] else ("edited" if counts["edited"] else None)
    has_delta = total_added > 0 or total_removed > 0

    parts: list[str] = []
    for key in _GROUP_ORDER:
        n = counts[key]
        if not n:
            continue
        phrase = _GROUP_PHRASE[key].format(n=n)
        if key == last_file_key and has_delta:
            delta = []
            if total_added > 0:
                delta.append(f"<font color='green'>+{total_added}</font>")
            if total_removed > 0:
                delta.append(f"<font color='red'>-{total_removed}</font>")
            phrase += " " + " ".join(delta)
        parts.append(phrase)
    return ", ".join(parts)


def _md(content: str) -> dict:
    return {"tag": "markdown", "content": content}


def _one_line_head(s) -> str:
    if not s:
        return ""
    s = str(s)
    return s[:_ERR_MAX] + ("…" if len(s) > _ERR_MAX else "")


def _clip_body(text: str) -> str:
    """Tail-cap a text run (keep the latest output). Cut on a line boundary and re-balance
    code fences so a slice through a ``` block doesn't corrupt the rest of the card."""
    if len(text) <= _BODY_MAX:
        return text
    clipped = text[-_BODY_MAX:]
    nl = clipped.find("\n")
    if nl != -1:
        clipped = clipped[nl + 1:]
    if clipped.count("```") % 2:       # odd fence count → we cut inside a code block; reopen it
        clipped = "```\n" + clipped
    return "…\n" + clipped


def _steps_md(steps) -> str:
    shown = steps[-_STEPS_MAX:]
    hidden = len(steps) - len(shown)
    lines: list[str] = []
    if hidden > 0:
        lines.append(f"… 更早 {hidden} 步")
    for st in shown:
        row = f"{_GLYPH.get(st.status, '•')} **{st.name}**"
        if st.summary:
            row += f"  `{st.summary.replace('`', 'ˋ')}`"
        lines.append(row)
    return "\n".join(lines)


def _steps_panel(steps) -> dict:
    """A contiguous run of tool steps as an ALWAYS-folded collapsible_panel. The header is
    the agent-UI aggregated run summary (grouped counts + line deltas); while nothing groups
    yet (e.g. a lone still-running Edit) it falls back to a plain step count. The folded body
    lists ``glyph name summary`` rows."""
    title = _run_summary(steps) or f"{len(steps)} 个工具步骤"
    return {
        "tag": "collapsible_panel",
        "expanded": False,                     # created folded; never patched to expanded
        "header": {"title": {"tag": "markdown", "content": title}},
        "elements": [_md(_steps_md(steps))],
    }


def render_card(state: StreamState, *, final: bool, dots: int = 3) -> dict:
    """Build the card-json-v2 dict for a StreamState snapshot. Walks ``timeline`` in order,
    emitting a markdown block per contiguous text run and a folded panel per contiguous
    run of tool steps. ``final=False`` appends a gray "Thinking" + ``dots`` footer."""
    elements: list[dict] = []

    tl = state.timeline
    i, n = 0, len(tl)
    while i < n:
        if isinstance(tl[i], ToolStep):
            j = i
            group = []
            while j < n and isinstance(tl[j], ToolStep):
                group.append(tl[j])
                j += 1
            elements.append(_steps_panel(group))
            i = j
        else:                                   # contiguous text run
            j = i
            texts = []
            while j < n and not isinstance(tl[j], ToolStep):
                if isinstance(tl[j], str) and tl[j]:
                    texts.append(tl[j])
                j += 1
            if texts:
                elements.append(_md(_clip_body("\n".join(texts))))
            i = j

    # error headline first, so it is prominent and never clipped by a long text run
    if final and state.is_error:
        et = _one_line_head(state.error_text)
        elements.insert(0, _md(f"⚠️ {et}" if et else "⚠️ 运行出错"))

    if final:
        if not elements:
            elements.append(_md("(无输出)"))
    else:
        # animated footer: "Thinking" + 1..3 dots (worker cycles `dots`)
        elements.append(_md(f"<font color='grey'>Thinking{'.' * max(1, min(3, dots))}</font>"))

    return {"schema": "2.0", "config": {"update_multi": True}, "body": {"elements": elements}}
