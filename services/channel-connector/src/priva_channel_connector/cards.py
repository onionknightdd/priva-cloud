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
    """A contiguous run of tool steps as an ALWAYS-folded collapsible_panel. The header
    counts them; the folded body lists ``glyph name summary`` rows."""
    running = sum(1 for s in steps if s.status == "running")
    title = f"步骤 ({len(steps)})" if running else f"步骤 ({len(steps)}) · 完成"
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
