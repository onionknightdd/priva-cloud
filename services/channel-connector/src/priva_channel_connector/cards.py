"""Pure Feishu interactive-card (schema 2.0 / card-json-v2) renderer over a StreamState.

No ``lark_oapi`` import — this only builds the card *dict*; the transport serializes and
sends/patches it. Kept separate from ``sse.py`` (pure reducer) and ``lark_ws.py`` (I/O) to
preserve the seam split, and unit-testable on its own.

Schema 2.0: ``{schema:"2.0", config, body:{elements:[...]}}``. NOTE: 2.0 dropped the
``note`` element (Feishu rejects it: "unsupported tag note"), so the running footer is a
gray ``markdown`` element, not a ``note``. create + patch of this structure were validated
live against Feishu.

Design decisions (2026-07-16, user-confirmed):
  - NO card header; content renders in MESSAGE ORDER off ``StreamState.timeline``.
  - WHILE STREAMING (``final=False``): the whole process — every text run AND every tool
    step — is shown EXPANDED inline, so the user watches each step live; a gray "Thinking"
    footer (dots cycling 1→2→3, animated by the worker patching ``dots``) signals activity.
  - ON THE FINAL CARD (``final=True``, after the ``result`` event): only the LAST text run
    (the answer) stays expanded; everything before it — intermediate text + all tool steps —
    collapses into ONE always-folded ``collapsible_panel`` on top, headed
    ``已运行:<elapsed>, <run summary>``. Inside it EACH tool is its own sub-fold (header =
    glyph/name/summary, body = full input + output as code blocks, which Feishu natively
    collapses into "N 行代码" widgets — we do NOT stack our own fold on top). The outer panel first appears on
    the final card (it replaces the inline blocks that occupied that slot while streaming), so
    Feishu sees a new element and honours ``expanded:false`` — we never depend on
    force-collapsing a panel the user may have opened, which Feishu won't do on patch (it
    treats ``expanded`` as the panel's initial state only).
  - the answer is top-level, so GFM markdown tables in it are promoted to NATIVE ``table``
    elements (Feishu rejects ``table`` inside a collapsible_panel — tables in the folded
    process stay markdown);
  - all fold headers carry a left chevron that rotates 180° when open;
  - an error is shown by a ⚠️ prefix element on top (no coloured header);
  - tool step rows = glyph + tool name + one-line input summary.
"""

from __future__ import annotations

import re

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
                "bash", "webFetch", "webSearch", "canvas", "asked", "other")
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
    "asked": "提出了 {n} 个问题",
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
        elif st.name == "AskUserQuestion":
            # counted in QUESTIONS (one call may carry several), phrased 提出了 N 个问题
            inp = st.tool_input if isinstance(st.tool_input, dict) else {}
            qs = inp.get("questions")
            counts["asked"] += len(qs) if isinstance(qs, list) and qs else 1
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
    return f"{_TRUNC_HINT}\n…\n" + clipped


# --- final-card layout: per-tool folds, native tables ----------------------
_OUTPUT_MAX_LINES = 200        # cap on rendered output lines (Feishu 30KB card budget)…
_OUTPUT_MAX_CHARS = 4000       # …and on chars, whichever bites first
_INPUT_MAX = 1500              # cap on a rendered tool-input block
_FOLD_ICON_TOKEN = "down-small-ccm_outlined"   # small chevron; validated live
# Shown (grey, OUTSIDE any code fence so it renders) wherever the card caps content for size.
_TRUNC_HINT = "<font color='grey'>（由于内容长度限制，无法完整显示）</font>"


def _fold_header(title: str) -> dict:
    """collapsible_panel header with the fold chevron at the LEFT, rotating 180° when open."""
    return {
        "title": {"tag": "markdown", "content": title},
        "icon": {"tag": "standard_icon", "token": _FOLD_ICON_TOKEN, "color": "grey", "size": "16px 16px"},
        "icon_position": "left",
        "icon_expanded_angle": -180,
    }


def _panel(title: str, elements: list, *, expanded: bool = False) -> dict:
    return {"tag": "collapsible_panel", "expanded": expanded,
            "header": _fold_header(title), "elements": elements}


def _fmt_duration(ms) -> str:
    """``duration_ms`` → ``Xm Ys`` when ≥60s else ``Ys``. Empty string when unavailable."""
    if not isinstance(ms, (int, float)) or ms < 0:
        return ""
    total = int(ms // 1000)
    return f"{total}s" if total < 60 else f"{total // 60}m {total % 60}s"


def _code(text: str, lang: str = "") -> str:
    return f"```{lang}\n{text}\n```"


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


def _walk_blocks(tl) -> list[tuple[str, object]]:
    """Collapse ``timeline`` into ordered blocks: ``("text", joined_str)`` for a contiguous
    run of text and ``("tools", [ToolStep, ...])`` for a contiguous run of tool steps."""
    blocks: list[tuple[str, object]] = []
    i, n = 0, len(tl)
    while i < n:
        if isinstance(tl[i], ToolStep):
            j = i
            group: list = []
            while j < n and isinstance(tl[j], ToolStep):
                group.append(tl[j])
                j += 1
            blocks.append(("tools", group))
            i = j
        else:
            j = i
            texts: list[str] = []
            while j < n and not isinstance(tl[j], ToolStep):
                if isinstance(tl[j], str) and tl[j]:
                    texts.append(tl[j])
                j += 1
            blocks.append(("text", "\n".join(texts)))
            i = j
    return blocks


def _blocks_to_elements(blocks) -> list[dict]:
    """Render walked blocks as EXPANDED inline elements: a markdown block per text run, a
    ``glyph name summary`` step-rows markdown per tool run. Used both for the streaming card
    and, verbatim, as the folded body of the final "过程" panel."""
    els: list[dict] = []
    for kind, payload in blocks:
        if kind == "text":
            if payload:
                els.append(_md(_clip_body(payload)))
        else:
            els.append(_md(_steps_md(payload)))
    return els


# --- per-tool fold (final card) --------------------------------------------
_INPUT_FIELD = {"Read": "file_path", "NotebookEdit": "notebook_path",
                "Grep": "pattern", "Glob": "pattern",
                "WebFetch": "url", "WebSearch": "query", "Task": "description"}


def _tool_title(st) -> str:
    """Per-tool fold header: glyph + name + one-line input summary (+ green/red line delta for
    a successful Edit/Write, same as the aggregated summary)."""
    title = f"{_GLYPH.get(st.status, '•')} **{st.name}**"
    if st.summary:
        title += f"  `{st.summary.replace('`', 'ˋ')}`"
    if st.name in ("Edit", "Write") and st.status == "done":
        added, removed = _line_delta(st.name, st.tool_input)
        d = []
        if added:
            d.append(f"<font color='green'>+{added}</font>")
        if removed:
            d.append(f"<font color='red'>-{removed}</font>")
        if d:
            title += "  " + " ".join(d)
    return title


def _tool_input_elements(st) -> list[dict]:
    """The tool's FULL input for the fold body: Bash→command, Edit→diff, Write→content,
    single-field tools→their key field, else the first string value. A ``_TRUNC_HINT`` note is
    appended (outside the code fence) when the input is capped at ``_INPUT_MAX``. []  when empty."""
    inp = st.tool_input if isinstance(st.tool_input, dict) else {}
    name = st.name
    content: str | None = None
    trunc = False
    if name == "Bash":
        cmd = str(inp.get("command", "")).strip()
        if cmd:
            content, trunc = _code(cmd[:_INPUT_MAX], "bash"), len(cmd) > _INPUT_MAX
    elif name == "Edit":
        old, new = str(inp.get("old_string", "")), str(inp.get("new_string", ""))
        diff = "\n".join(["- " + ln for ln in old.split("\n")] + ["+ " + ln for ln in new.split("\n")])
        head = f"`{inp['file_path']}`\n" if inp.get("file_path") else ""
        content, trunc = head + _code(diff[:_INPUT_MAX], "diff"), len(diff) > _INPUT_MAX
    elif name == "Write":
        c = str(inp.get("content", ""))
        head = f"`{inp['file_path']}`\n" if inp.get("file_path") else ""
        content, trunc = head + _code(c[:_INPUT_MAX]), len(c) > _INPUT_MAX
    else:
        field = _INPUT_FIELD.get(name)
        val = str(inp[field]) if field and inp.get(field) else \
            next((v for v in inp.values() if isinstance(v, str) and v), "")
        if val:
            content, trunc = f"`{val[:_INPUT_MAX]}`", len(val) > _INPUT_MAX
    if content is None:
        return []
    return [_md(content)] + ([_md(_TRUNC_HINT)] if trunc else [])


def _tool_output_elements(st) -> list[dict]:
    """The tool's output as a SINGLE code block. Feishu natively collapses a long code block
    into an expandable "N 行代码" widget (expands in place), so we do NOT hand-roll a fold —
    stacking our own fold on top of Feishu's produced a confusing double-collapse. Capped by
    lines/chars for the 30KB card budget, with a grey size hint appended (outside the fence)
    when truncated. Empty when there's no output."""
    out = (st.result_text or "").rstrip("\n")
    if not out:
        return []
    truncated = False
    if len(out) > _OUTPUT_MAX_CHARS:
        out, truncated = out[:_OUTPUT_MAX_CHARS], True
    lines = out.split("\n")
    if len(lines) > _OUTPUT_MAX_LINES:
        lines, truncated = lines[:_OUTPUT_MAX_LINES], True
    els = [_md(_code("\n".join(lines)))]
    if truncated:
        els.append(_md(_TRUNC_HINT))              # grey hint OUTSIDE the code fence so it renders
    return els


# AskUserQuestion's synthesized tool_result is `... "Q1"="A1". "Q2"="A2". ...`; pull the pairs
# out so a resolved question renders in the clean "已收到你的选择" style instead of that raw dump.
_ASKUSER_QA_RE = re.compile(r'"([^"]+)"\s*=\s*"([^"]*)"')


def _askuser_output_elements(st) -> list[dict]:
    """A resolved AskUserQuestion → the chosen answers in the '✅ 已收到你的选择' style (green
    header + one grey ``Q -> A`` line each), parsed from the tool_result, instead of the raw
    ``Your questions have been answered: …`` code block. Falls back to the raw output if the
    text doesn't parse (so nothing is ever lost)."""
    out = (st.result_text or "").strip()
    if not out:
        return []
    pairs = _ASKUSER_QA_RE.findall(out)
    if not pairs:
        return _tool_output_elements(st)
    rows = ["<font color='green'>✅ 已收到你的选择</font>"]
    rows += [f"<font color='grey'>{q} -> {a}</font>" for q, a in pairs]
    return [_md("\n".join(rows))]


def _askuser_question_elements(st) -> list[dict]:
    """AskUserQuestion → ONLY the question texts (one ❓ line each). The options/config
    dump is deliberately never shown — the interactive card already presented them."""
    inp = st.tool_input if isinstance(st.tool_input, dict) else {}
    qs = [q.get("question") for q in (inp.get("questions") or []) if isinstance(q, dict)]
    qs = [q for q in qs if q]
    if not qs:
        return []
    return [_md("\n".join(f"❓ {q}" for q in qs))]


def _tool_panel(st) -> dict:
    """One tool as its own folded panel: header = glyph/name/summary, body = full input then
    output, each a code block (Feishu natively collapses long code blocks in place).
    AskUserQuestion is special-cased: resolved → the clean answer style, otherwise just the
    question texts — the raw input JSON (options/config) is never dumped."""
    if st.name == "AskUserQuestion":
        if st.status == "done":
            body = _askuser_output_elements(st) or _askuser_question_elements(st)
        else:
            body = _askuser_question_elements(st)
        return _panel(_tool_title(st), body or [_md("`(无输入/输出)`")])
    body = _tool_input_elements(st) + _tool_output_elements(st)
    if not body:
        body.append(_md("`(无输入/输出)`"))
    return _panel(_tool_title(st), body)


# --- native GFM-table conversion (top-level answer only) --------------------
# Feishu rejects `table` inside a collapsible_panel, so this runs ONLY on the top-level answer.
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")


def _split_cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _md_table_to_native(header: list[str], rows: list[list[str]]) -> dict:
    cols = [{"name": f"c{i}", "display_name": h or " ", "data_type": "lark_md",
             "horizontal_align": "left", "width": "auto"} for i, h in enumerate(header)]
    trows = [{f"c{i}": (rc[i] if i < len(rc) else "") for i in range(len(header))} for rc in rows]
    return {"tag": "table", "page_size": max(1, min(len(trows) or 1, 10)), "row_height": "low",
            "header_style": {"text_align": "left", "background_style": "grey", "bold": True, "lines": 1},
            "columns": cols, "rows": trows}


def _answer_elements(text: str) -> list[dict]:
    """Top-level answer → elements: GFM markdown tables become NATIVE ``table`` elements, the
    rest stays markdown. A table = a pipe row immediately followed by a ``|---|---|`` separator,
    then its pipe body rows."""
    lines = text.split("\n")
    els: list[dict] = []
    buf: list[str] = []

    def flush():
        if buf:
            content = "\n".join(buf).strip("\n")
            if content.strip():
                els.append(_md(_clip_body(content)))
            buf.clear()

    i, n = 0, len(lines)
    while i < n:
        if i + 1 < n and "|" in lines[i] and _TABLE_SEP_RE.match(lines[i + 1]):
            header = _split_cells(lines[i])
            j = i + 2
            rows = []
            while j < n and lines[j].strip() and "|" in lines[j]:
                rows.append(_split_cells(lines[j]))
                j += 1
            flush()
            els.append(_md_table_to_native(header, rows))
            i = j
        else:
            buf.append(lines[i])
            i += 1
    flush()
    return els


def _process_panel(items, duration_ms=None) -> dict | None:
    """Fold the whole *process* (intermediate text runs + every tool, in message order) into
    ONE top-level folded panel. Each contiguous tool run expands into per-tool sub-panels.
    Header = ``已运行:<elapsed>, <run summary>`` (elapsed from the run's ``duration_ms``);
    falls back to ``过程 · <summary>`` when no duration. Returns None when nothing folds."""
    body: list[dict] = []
    for kind, payload in _walk_blocks(items):
        if kind == "text":
            if payload:
                body.append(_md(_clip_body(payload)))   # markdown (tables stay md inside folds)
        else:
            body.extend(_tool_panel(st) for st in payload)
    if not body:
        return None
    steps = [it for it in items if isinstance(it, ToolStep)]
    summary = _run_summary(steps) or (f"{len(steps)} 个工具步骤" if steps else "")
    dur = _fmt_duration(duration_ms)
    if dur:
        header = f"已运行:{dur}" + (f", {summary}" if summary else "")
    else:
        header = f"过程 · {summary}" if summary else "过程"
    return _panel(header, body)


def render_card(state: StreamState, *, final: bool, dots: int = 3) -> dict:
    """Build the card-json-v2 dict for a StreamState snapshot.

    ``final=False`` (streaming): the whole timeline renders EXPANDED inline (text runs +
    tool-step rows, in message order) followed by a gray "Thinking" + ``dots`` footer.

    ``final=True`` (result received): the LAST text run is split off as the answer (expanded,
    at the bottom, with GFM tables promoted to native ``table`` elements); everything before it
    collapses into one folded "已运行" panel on top, each tool its own sub-fold. An error
    headline (⚠️) is prepended; an empty run shows ``(无输出)``, a run that ends on a tool with
    no closing text shows ``(无文本回复)``."""
    tl = state.timeline

    if not final:
        elements = _blocks_to_elements(_walk_blocks(tl))
        if state.pending_prompt is not None:
            # An AskUserQuestion / permission prompt is live — render it INLINE (interactive)
            # instead of the Thinking footer. The worker pauses the ticker while this is set,
            # so the embedded dropdown/input isn't wiped by a patch mid-interaction.
            from . import permission_cards
            elements.extend(permission_cards.prompt_elements(state.pending_prompt))
        else:
            elements.append(_md(f"<font color='grey'>Thinking{'.' * max(1, min(3, dots))}</font>"))
        return {"schema": "2.0", "config": {"update_multi": True}, "body": {"elements": elements}}

    # Split the trailing contiguous text run off as THE answer; fold the rest.
    k = len(tl)
    while k > 0 and isinstance(tl[k - 1], str):
        k -= 1
    process = tl[:k]
    answer = "\n".join(t for t in tl[k:] if isinstance(t, str) and t)

    elements: list[dict] = []
    if state.is_error:                          # prominent, on top, never clipped by a text run
        et = _one_line_head(state.error_text)
        elements.append(_md(f"⚠️ {et}" if et else "⚠️ 运行出错"))

    panel = _process_panel(process, state.duration_ms)
    if panel is not None:
        elements.append(panel)

    if answer:
        elements.extend(_answer_elements(answer))       # top-level → GFM tables become native
    elif panel is not None and not state.is_error:
        elements.append(_md("(无文本回复)"))

    if not elements:
        elements.append(_md("(无输出)"))

    return {"schema": "2.0", "config": {"update_multi": True}, "body": {"elements": elements}}
