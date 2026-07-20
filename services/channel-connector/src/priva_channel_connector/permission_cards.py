"""Pure renderer for interactive AskUserQuestion / permission cards (Feishu card-json-v2).

Separate from ``cards.py`` (streaming StreamState renderer) — this builds the question/answer
cards a run blocks on. No lark_oapi import; returns plain dicts (the transport serializes).

Layout (all pieces validated live against Feishu, 2026-07-18):
  - Model ① (exactly 1 question, single-select): a ``select_static`` dropdown whose options are
    the AUQ options plus a synthetic '💡 我有其他的想法'. Picking a normal option resolves
    immediately (the standalone select fires an on-change callback — verified); picking '其他'
    reveals a text-input ``form``. The dropdown stays after reveal (``initial_option``) so the
    choice is reversible.
  - Model ② (multiSelect or >1 question): one ``form`` — each question a ``select_static``
    (single) or a group of ``checker`` toggles (multi) — with one ``form_submit`` button.
  - Both carry a 跳过 button; kind="permission" is a plain 允许/拒绝 pair.

Answer format matches the pod's ``_askuser_answers_map`` (and the web ``buildAnswerText``):
one line per answered question, ``- {header|question} -> {v1; v2}``.

Routing: card actions are correlated to the PendingPrompt by message_id (see ``pending``),
so option ``value``s only need to be locally unique — normal options use their index ``oJ``,
the custom sentinel is ``__other__``, buttons carry ``{"act": ...}``.
"""

from __future__ import annotations

OTHER = "__other__"
SKIP = "skip"
CUSTOM_FIELD = "custom"
_MAXLEN = 300


def _pt(s: str) -> dict:
    return {"tag": "plain_text", "content": s}


def _md(content: str) -> dict:
    return {"tag": "markdown", "content": content}


def _card(elements: list) -> dict:
    return {"schema": "2.0", "config": {"update_multi": True}, "body": {"elements": elements}}


def _head(q: dict) -> str:
    return str(q.get("header") or q.get("question") or "").strip()


def _chip(q: dict) -> str:
    header = str(q.get("header") or "").strip()
    question = str(q.get("question") or "").strip()
    return f"**[{header}]** {question}" if header else f"**{question}**"


def _opt_display(opt) -> str:
    if not isinstance(opt, dict):
        return str(opt)
    label = str(opt.get("label", "")).strip()
    desc = str(opt.get("description", "")).strip()
    return f"{label} · {desc}" if desc else label


_PERM_FIELD = {"Read": "file_path", "Write": "file_path", "Edit": "file_path",
               "NotebookEdit": "notebook_path", "Grep": "pattern", "Glob": "pattern",
               "WebFetch": "url", "WebSearch": "query", "Task": "description"}
_PERM_LABEL = {"Read": "读取", "Write": "写入", "Edit": "编辑", "NotebookEdit": "编辑",
               "Grep": "搜索", "Glob": "匹配", "WebFetch": "抓取", "WebSearch": "搜索", "Task": "子任务"}


def _perm_input_elements(prompt) -> list[dict]:
    """Show WHAT is being approved: Bash → the command (code block); other tools → their key
    field (file path / pattern / url) inline. Empty when there's no input."""
    inp = getattr(prompt, "tool_input", None)
    if not isinstance(inp, dict):
        return []
    name = prompt.tool_name
    if name == "Bash":
        cmd = " ".join(str(inp.get("command", "")).split())[:1000].replace("`", "ˋ")
        return [_md(f"执行的命令:`{cmd}`")] if cmd else []
    field = _PERM_FIELD.get(name)
    val = inp.get(field) if field else None
    if not val:
        val = next((v for v in inp.values() if isinstance(v, str) and v), "")
    if not val:
        return []
    val = str(val)[:500].replace("`", "ˋ")
    return [_md(f"{_PERM_LABEL.get(name, '参数')}:`{val}`")]


def _intro(prompt) -> list[dict]:
    if prompt.kind == "permission":
        rows = [_md("🔐 **需要你确认操作**")]
        if prompt.tool_name:
            rows.append(_md(f"工具:`{prompt.tool_name}`"))
        rows += _perm_input_elements(prompt)
        if prompt.reason:
            rows.append(_md(f"<font color='grey'>{prompt.reason}</font>"))
        return rows
    n = len(prompt.questions or [])
    return [_md("🤔 **需要你确认**" + (f" · {n} 个问题" if n > 1 else ""))]


def _skip_button() -> dict:
    return {"tag": "button", "text": _pt("跳过 · 让我直接说"), "type": "default", "width": "default",
            "behaviors": [{"type": "callback", "value": {"act": SKIP}}]}


def _select_options(q: dict, *, with_other: bool) -> list[dict]:
    opts = [{"text": _pt(_opt_display(o)), "value": f"o{j}"}
            for j, o in enumerate(q.get("options") or [])]
    if with_other:
        opts.append({"text": _pt("💡 我有其他的想法"), "value": OTHER})
    return opts


# --- render ----------------------------------------------------------------
def prompt_elements(prompt) -> list[dict]:
    """The interactive elements ONLY (no card wrapper) — for embedding into the streaming
    process card. Reads ``prompt.reveal`` so the model① custom input shows after '其他'."""
    if prompt.kind == "permission":
        return _confirm_elements(prompt)
    qs = prompt.questions or []
    single = len(qs) == 1 and not qs[0].get("multiSelect")
    if single:
        return _model1_elements(prompt, reveal=bool(getattr(prompt, "reveal", False)))
    return _model2_elements(prompt)


def permission_card(prompt) -> dict:
    """Standalone card (fallback when there's no streaming card to embed into, + tests)."""
    return _card(prompt_elements(prompt))


def reveal_card(prompt) -> dict:
    """Model ① after picking '我有其他的想法': dropdown kept (其他 pre-selected) + input form."""
    return _card(_model1_elements(prompt, reveal=True))


def _model1_elements(prompt, *, reveal: bool = False) -> list[dict]:
    q = (prompt.questions or [{}])[0]
    selected = getattr(prompt, "selected", "") or ""
    els = _intro(prompt)
    els.append(_md(_chip(q)))
    select = {"tag": "select_static", "name": "q0", "placeholder": _pt("请选择"),
              "options": _select_options(q, with_other=True),
              "behaviors": [{"type": "callback", "value": {"act": "select"}}]}
    if reveal:
        select["initial_option"] = OTHER            # keep 其他 shown so the choice is reversible
    elif selected:
        select["initial_option"] = selected         # keep the pick visible after re-render
    els.append(select)
    if reveal:
        # picking 其他 reveals a custom input with its OWN submit (form_submit).
        els.append({"tag": "form", "name": "custom_form", "elements": [
            {"tag": "input", "name": CUSTOM_FIELD, "placeholder": _pt("说说你的想法…"),
             "label": _pt("自定义回答"), "label_position": "top", "max_length": _MAXLEN},
            {"tag": "button", "text": _pt("提交 →"), "type": "primary",
             "action_type": "form_submit", "name": "submit"}]})
    else:
        # explicit submit — the pick is only sent when this is clicked (no auto-submit on select).
        els.append({"tag": "button", "text": _pt("提交 →"), "type": "primary", "width": "default",
                    "behaviors": [{"type": "callback", "value": {"act": "submit"}}]})
    els.append(_skip_button())
    return els


def _model2_elements(prompt) -> list[dict]:
    els = _intro(prompt)
    form_elements: list[dict] = []
    for i, q in enumerate(prompt.questions or []):
        multi = bool(q.get("multiSelect"))
        form_elements.append(_md(_chip(q) + (" <font color='grey'>多选</font>" if multi
                                             else " <font color='grey'>单选</font>")))
        opts = q.get("options") or []
        if multi:
            for j, o in enumerate(opts):
                form_elements.append({"tag": "checker", "name": f"q{i}o{j}",
                                      "text": _pt(_opt_display(o)), "checked": False})
        else:
            form_elements.append({"tag": "select_static", "name": f"q{i}", "placeholder": _pt("请选择"),
                                  "options": [{"text": _pt(_opt_display(o)), "value": f"o{j}"}
                                              for j, o in enumerate(opts)]})
    form_elements.append({"tag": "button", "text": _pt("提交 →"), "type": "primary",
                          "action_type": "form_submit", "name": "submit"})
    els.append({"tag": "form", "name": "auq_form", "elements": form_elements})
    els.append(_skip_button())
    return els


def _confirm_elements(prompt) -> list[dict]:
    els = _intro(prompt)
    els.append({"tag": "button", "text": _pt("✅ 允许"), "type": "primary", "width": "fill",
                "behaviors": [{"type": "callback", "value": {"act": "allow"}}]})
    els.append({"tag": "button", "text": _pt("✋ 拒绝"), "type": "default", "width": "fill",
                "behaviors": [{"type": "callback", "value": {"act": "deny"}}]})
    return els


# --- terminal cards --------------------------------------------------------
def answered_card(prompt, answer_text: str) -> dict:
    rows = [_md("<font color='green'>✅ 已收到你的选择</font>")]
    for ln in (answer_text or "").split("\n"):
        ln = ln.strip()
        if ln.startswith("-"):
            ln = ln[1:].strip()
        if ln:
            rows.append(_md(f"<font color='grey'>{ln}</font>"))
    return _card(rows)


def skipped_card(prompt) -> dict:
    return _card([_md("<font color='grey'>⏭ 已跳过 —— 直接把你的想法回复给我即可</font>")])


def timeout_card(prompt) -> dict:
    return _card([_md("<font color='grey'>⏱ 已超时,本次未作答</font>")])


# --- answer building (card selections -> locked answer string) -------------
def _q_values(q: dict, i: int, form_value: dict) -> list[str]:
    opts = q.get("options") or []
    if q.get("multiSelect"):
        return [_opt_display(opts[j]) for j in range(len(opts)) if form_value.get(f"q{i}o{j}")]
    v = form_value.get(f"q{i}")
    if isinstance(v, str) and v.startswith("o") and v[1:].isdigit():
        j = int(v[1:])
        if 0 <= j < len(opts):
            return [_opt_display(opts[j])]
    return [str(v)] if v else []


def answer_from_form(questions, form_value: dict | None) -> str:
    """Build the locked answer from a form_submit's ``form_value`` (all named inputs).
    The model① reveal path carries only ``custom`` (its select sits outside the form) — that
    text is attached to the first question."""
    form_value = form_value or {}
    lines: list[str] = []
    for i, q in enumerate(questions or []):
        vals = _q_values(q, i, form_value)
        if i == 0 and str(form_value.get(CUSTOM_FIELD) or "").strip():
            vals.append(str(form_value[CUSTOM_FIELD]).strip())
        if vals:
            lines.append(f"- {_head(q)} -> {'; '.join(vals)}")
    return "\n".join(lines)


def answer_from_option(questions, option_value: str) -> str:
    """Model ① normal dropdown pick (``option_value`` == 'oJ') → single answer line."""
    q = (questions or [{}])[0]
    opts = q.get("options") or []
    if isinstance(option_value, str) and option_value.startswith("o") and option_value[1:].isdigit():
        j = int(option_value[1:])
        if 0 <= j < len(opts):
            return f"- {_head(q)} -> {_opt_display(opts[j])}"
    return ""
