"""Skill archive upload: decompression bombs and path containment.

MAX_UPLOAD_SIZE bounds the COMPRESSED upload only. Deflate reaches ~1000:1, so a
3 MB archive that passes that check could expand to gigabytes; the old extractor
called ``zf.read()`` with no bound and OOM'd the runner pod on one authenticated
request. Declared sizes come from attacker-controlled headers, so the actual
reads are bounded too.
"""

from __future__ import annotations

import io
import tarfile
import zipfile

import pytest
from fastapi import HTTPException

from priva_agent_runner.services import skills

SKILL_MD = b"---\nname: demo\ndescription: A demo skill for tests.\n---\n\nbody\n"


def _zip(entries: dict[str, bytes], compresslevel: int = 9) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=compresslevel) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _targz(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


# --- decompression bombs ------------------------------------------------------


def test_zip_bomb_is_rejected_before_it_is_read():
    """~50 MB of zeros deflates to a few KB — comfortably inside MAX_UPLOAD_SIZE."""
    payload = _zip({"demo/SKILL.md": SKILL_MD, "demo/big.bin": b"\0" * (60 * 1024 * 1024)})
    assert len(payload) < skills.MAX_UPLOAD_SIZE  # the upload cap does not catch it
    with pytest.raises(HTTPException) as exc:
        skills._extract_zip(payload)
    assert exc.value.status_code == 400


def test_extreme_ratio_is_rejected_even_when_every_limit_passes():
    """Each entry under MAX_ENTRY_BYTES and the total under MAX_TOTAL_BYTES, so
    only the compression-ratio guard can catch this one."""
    entry = b"\0" * (4 * 1024 * 1024)
    payload = _zip({"demo/SKILL.md": SKILL_MD,
                    **{f"demo/f{i}.bin": entry for i in range(9)}})  # 36MB < 50MB
    with pytest.raises(HTTPException, match="bomb"):
        skills._extract_zip(payload)


def test_targz_bomb_is_rejected():
    payload = _targz({"demo/SKILL.md": SKILL_MD, "demo/big.bin": b"\0" * (60 * 1024 * 1024)})
    assert len(payload) < skills.MAX_UPLOAD_SIZE
    with pytest.raises(HTTPException) as exc:
        skills._extract_tar(payload, "r:gz")
    assert exc.value.status_code == 400


def test_single_oversized_entry_is_rejected():
    payload = _zip({"demo/SKILL.md": SKILL_MD, "demo/one.bin": b"\0" * (6 * 1024 * 1024)})
    with pytest.raises(HTTPException, match="uncompressed"):
        skills._extract_zip(payload)


def test_too_many_entries_is_rejected():
    payload = _zip({f"demo/f{i}": b"x" for i in range(skills.MAX_ARCHIVE_ENTRIES + 1)})
    with pytest.raises(HTTPException, match="too many entries"):
        skills._extract_zip(payload)


def test_a_lying_header_cannot_smuggle_a_large_entry(monkeypatch):
    """Declared sizes are attacker-controlled; the read itself must be bounded."""
    payload = _zip({"demo/SKILL.md": SKILL_MD, "demo/big.bin": b"\0" * (2 * 1024 * 1024)})
    monkeypatch.setattr(skills, "MAX_ENTRY_BYTES", 1024)      # pretend it declared small
    monkeypatch.setattr(skills, "MAX_TOTAL_BYTES", 1024)
    zf = zipfile.ZipFile(io.BytesIO(payload))
    budget = [1024]
    with pytest.raises(HTTPException, match="larger than its declared size"):
        with zf.open("demo/big.bin") as fh:
            skills._read_bounded(fh, budget)


def test_total_budget_is_shared_across_members():
    """Many individually-legal members must not add up past the total."""
    each = b"\0" * (4 * 1024 * 1024)
    names = [f"demo/f{i}.bin" for i in range(20)]        # 80MB total, 4MB each
    payload = _zip({"demo/SKILL.md": SKILL_MD, **{n: each for n in names}})
    with pytest.raises(HTTPException):
        skills._extract_zip(payload)


# --- entry types & containment -----------------------------------------------


def test_tar_symlink_entries_are_refused():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("demo/SKILL.md")
        info.size = len(SKILL_MD)
        tf.addfile(info, io.BytesIO(SKILL_MD))
        link = tarfile.TarInfo("demo/escape")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tf.addfile(link)
    with pytest.raises(HTTPException, match="unsupported entry type"):
        skills._extract_tar(buf.getvalue(), "r:gz")


def test_safe_resolve_rejects_sibling_prefix_escape(tmp_path):
    """`startswith` treated `…/skills-evil` as inside `…/skills`."""
    base = tmp_path / "skills"
    base.mkdir()
    (tmp_path / "skills-evil").mkdir()
    with pytest.raises(HTTPException, match="Path traversal"):
        skills._safe_resolve(base, "../skills-evil/pwned")
    with pytest.raises(HTTPException, match="Path traversal"):
        skills._safe_resolve(base, "../../etc/passwd")
    assert skills._safe_resolve(base, "legit/file.md") == base / "legit" / "file.md"


# --- the legitimate path still works -----------------------------------------


def test_a_normal_skill_archive_still_extracts():
    payload = _zip({"demo/SKILL.md": SKILL_MD, "demo/ref/notes.md": b"# notes"})
    members, read_file = skills._extract_zip(payload)
    assert "demo/SKILL.md" in members
    assert read_file("demo/SKILL.md") == SKILL_MD
    assert read_file("demo/ref/notes.md") == b"# notes"
    assert read_file("demo/missing") is None

    members, read_file = skills._extract_tar(_targz({"demo/SKILL.md": SKILL_MD}), "r:gz")
    assert read_file("demo/SKILL.md") == SKILL_MD


# --- path escape via empty components (regression) ----------------------------


def test_doubled_separator_cannot_write_outside_the_skill_dir(tmp_path):
    """`demo//tmp/pwn` has no `..`, passes a startswith("demo/") prefix test, and
    after stripping the top-level dir leaves `/tmp/pwn` — which `dest / relative`
    resolves to an ABSOLUTE path, discarding the base entirely."""
    with pytest.raises(HTTPException) as exc:
        skills._validate_member_name("demo//tmp/pwn")
    assert exc.value.status_code == 400

    # and the write path itself contains, independent of the name check
    base = tmp_path / "demo"
    base.mkdir()
    with pytest.raises(HTTPException, match="Path traversal"):
        skills._safe_resolve(base, "/tmp/pwn")


@pytest.mark.parametrize("bad", [
    "/etc/passwd",        # absolute
    "C:/windows/x",       # drive-absolute
    "demo/../../x",       # classic traversal
    "demo\\..\\x",        # backslash separators the POSIX split misses
    "demo/a\x00b",        # NUL
    "demo//x",            # empty component
    "",                   # empty name
])
def test_unsafe_member_names_are_refused(bad):
    with pytest.raises(HTTPException):
        skills._validate_member_name(bad)


@pytest.mark.parametrize("ok", ["demo/SKILL.md", "demo/", "demo/ref/notes.md"])
def test_ordinary_member_names_still_pass(ok):
    skills._validate_member_name(ok)


def test_upload_rejects_an_archive_with_an_escaping_member():
    payload = _zip({"demo/SKILL.md": SKILL_MD, "demo//tmp/pwn": b"owned"})
    with pytest.raises(HTTPException) as exc:
        skills.upload_skill("user", None, payload, "s.zip", "alice")
    assert exc.value.status_code == 400


# --- the Skill Hub route shares the same pipeline (regression) -----------------


def test_skill_hub_upload_is_bound_by_the_same_guards(tmp_path, monkeypatch):
    """Skill Hub carried a near-verbatim COPY of the parse+extract block. When the
    traversal guards were tightened in skills.py the copy kept the old
    `".." in parts` check and a bare `dest / relative` write, so the archive
    rejected on one route still escaped on the other."""
    from priva_agent_runner.services import skill_hub

    monkeypatch.setattr(skill_hub, "_runtime_skills_dir", lambda: tmp_path / "hub")
    marker = tmp_path / "pwn"

    escaping = _zip({"demo/SKILL.md": SKILL_MD, f"demo/{marker}": b"owned"})
    with pytest.raises(HTTPException) as exc:
        skill_hub.upload_hub_skill(escaping, "s.zip")
    assert exc.value.status_code == 400
    assert not marker.exists()

    # empty-component escape, the exact PoC shape
    with pytest.raises(HTTPException):
        skill_hub.upload_hub_skill(
            _zip({"demo/SKILL.md": SKILL_MD, "demo//tmp/pwn": b"owned"}), "s.zip")

    # bombs are bounded here too, now that both routes share read_archive()
    bomb = _zip({"demo/SKILL.md": SKILL_MD, "demo/big.bin": b"\0" * (60 * 1024 * 1024)})
    with pytest.raises(HTTPException):
        skill_hub.upload_hub_skill(bomb, "s.zip")

    # …and an ordinary bundle still installs
    ok = _zip({"demo/SKILL.md": SKILL_MD, "demo/ref/n.md": b"# n"})
    assert skill_hub.upload_hub_skill(ok, "s.zip").name == "demo"
    assert (tmp_path / "hub" / "demo" / "ref" / "n.md").read_bytes() == b"# n"
