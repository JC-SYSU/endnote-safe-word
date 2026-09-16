from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "packaging/build_release.py"


def load_builder():
    spec = importlib.util.spec_from_file_location("release_builder", BUILDER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_builder_reads_matching_version() -> None:
    assert load_builder().project_version()


def test_release_builder_creates_valid_source_independent_bundle(tmp_path: Path) -> None:
    output = tmp_path / "release"
    version = load_builder().project_version()
    result = subprocess.run(
        [sys.executable, str(BUILDER_PATH), "--output", str(output)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    bundle = output / f"endnote-safe-word-{version}"
    archive = output / f"endnote-safe-word-{version}.tar.gz"
    assert str(bundle) in result.stdout
    assert archive.is_file()
    assert (bundle / "install.sh").stat().st_mode & 0o111
    assert (bundle / "skill/word-document-safe-editing/SKILL.md").is_file()
    wheels = list((bundle / "wheels").glob("*.whl"))
    assert len(wheels) == 1
    load_builder().validate_wheel(wheels[0])

    checksum_lines = (bundle / "SHA256SUMS").read_text().splitlines()
    checked_paths = set()
    for line in checksum_lines:
        digest, relative = line.split("  ", 1)
        checked_paths.add(relative)
        assert hashlib.sha256((bundle / relative).read_bytes()).hexdigest() == digest
    assert "wheels/" + wheels[0].name in checked_paths
    assert not any(path.name in {".DS_Store", "__pycache__"} for path in bundle.rglob("*"))

    with tarfile.open(archive) as packaged:
        names = packaged.getnames()
    assert f"{bundle.name}/install.sh" in names
    assert not any(name.startswith(f"{bundle.name}/tests/") for name in names)


def test_installer_rejects_checksum_tampering(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "install.sh").write_bytes(
        (ROOT / "packaging/templates/install.sh").read_bytes()
    )
    (bundle / "VERSION").write_text("0.1.0a1\n")
    (bundle / "README.md").write_text("tampered\n")
    (bundle / "wheels").mkdir()
    (bundle / "wheels/endnote_safe_word-0.1.0a1-py3-none-any.whl").write_bytes(b"wheel")
    (bundle / "SHA256SUMS").write_text("0" * 64 + "  README.md\n")
    result = subprocess.run(
        ["bash", str(bundle / "install.sh")],
        env={"HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert not (tmp_path / "home/.local/share/endnote-safe-word").exists()


def test_installer_refuses_unmanaged_command_before_mutation(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    wheels = bundle / "wheels"
    wheels.mkdir(parents=True)
    files = {
        "VERSION": b"0.1.0a1\n",
        "install.sh": (ROOT / "packaging/templates/install.sh").read_bytes(),
        "wheels/endnote_safe_word-0.1.0a1-py3-none-any.whl": b"wheel",
    }
    for relative, content in files.items():
        path = bundle / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (bundle / "skill/word-document-safe-editing").mkdir(parents=True)
    (bundle / "skill/word-document-safe-editing/SKILL.md").write_text("skill\n")
    checksums = []
    for path in sorted(bundle.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS":
            checksums.append(
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(bundle).as_posix()}"
            )
    (bundle / "SHA256SUMS").write_text("\n".join(checksums) + "\n")

    home = tmp_path / "home"
    command = home / ".local/bin/endnote-safe-word"
    command.parent.mkdir(parents=True)
    command.write_text("unmanaged\n")
    result = subprocess.run(
        ["bash", str(bundle / "install.sh")],
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "not a managed symlink" in result.stderr
    assert not (home / ".local/share/endnote-safe-word").exists()


def test_installer_updates_managed_links_and_backs_up_skill(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    wheel = bundle / "wheels/endnote_safe_word-0.1.0a1-py3-none-any.whl"
    wheel.parent.mkdir(parents=True)
    wheel.write_bytes(b"wheel")
    (bundle / "VERSION").write_text("0.1.0a1\n")
    (bundle / "install.sh").write_bytes(
        (ROOT / "packaging/templates/install.sh").read_bytes()
    )
    skill = bundle / "skill/word-document-safe-editing/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("new skill\n")
    checksums = []
    for path in sorted(bundle.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS":
            checksums.append(
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(bundle).as_posix()}"
            )
    (bundle / "SHA256SUMS").write_text("\n".join(checksums) + "\n")

    home = tmp_path / "home"
    data_home = home / "data"
    version_dir = data_home / "versions/0.1.0a1"
    runtime_bin = version_dir / ".venv/bin"
    runtime_bin.mkdir(parents=True)
    (version_dir / ".endnote-safe-word-version").write_text("0.1.0a1\n")
    for command_name in ("endnote-safe-word", "word-document-safe-editing-mcp"):
        command = runtime_bin / command_name
        command.write_text("#!/bin/sh\n")
        command.chmod(0o755)
    old_skill = home / "skills/word-document-safe-editing/SKILL.md"
    old_skill.parent.mkdir(parents=True)
    old_skill.write_text("old skill\n")

    result = subprocess.run(
        ["bash", str(bundle / "install.sh")],
        env={
            "HOME": str(home),
            "PATH": "/usr/bin:/bin",
            "ENDNOTE_SAFE_WORD_DATA_HOME": str(data_home),
            "ENDNOTE_SAFE_WORD_BIN_HOME": str(home / "bin"),
            "CODEX_SKILLS_HOME": str(home / "skills"),
        },
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Previous Skill backed up" in result.stdout
    assert old_skill.read_text() == "new skill\n"
    backups = list(
        (home / "skills/.backups").glob("word-document-safe-editing-*/SKILL.md")
    )
    assert len(backups) == 1
    assert backups[0].read_text() == "old skill\n"
    assert (data_home / "current").readlink() == Path("versions/0.1.0a1")
    assert (home / "bin/endnote-safe-word").is_symlink()


def test_installer_migrates_legacy_skill_and_mcp_link(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    wheel = bundle / "wheels/endnote_safe_word-0.1.0a1-py3-none-any.whl"
    wheel.parent.mkdir(parents=True)
    wheel.write_bytes(b"wheel")
    (bundle / "VERSION").write_text("0.1.0a1\n")
    (bundle / "install.sh").write_bytes(
        (ROOT / "packaging/templates/install.sh").read_bytes()
    )
    skill = bundle / "skill/word-document-safe-editing/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("renamed skill\n")
    checksums = []
    for path in sorted(bundle.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS":
            checksums.append(
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(bundle).as_posix()}"
            )
    (bundle / "SHA256SUMS").write_text("\n".join(checksums) + "\n")

    home = tmp_path / "home"
    data_home = home / "data"
    version_dir = data_home / "versions/0.1.0a1"
    runtime_bin = version_dir / ".venv/bin"
    runtime_bin.mkdir(parents=True)
    (version_dir / ".endnote-safe-word-version").write_text("0.1.0a1\n")
    for command_name in ("endnote-safe-word", "word-document-safe-editing-mcp"):
        command = runtime_bin / command_name
        command.write_text("#!/bin/sh\n")
        command.chmod(0o755)
    bin_home = home / "bin"
    bin_home.mkdir()
    current = data_home / "current"
    current.symlink_to("versions/0.1.0a1")
    legacy_link = bin_home / "endnote-safe-word-mcp"
    legacy_link.symlink_to(current / ".venv/bin/endnote-safe-word-mcp")
    legacy_skill = home / "skills/endnote-safe-word/SKILL.md"
    legacy_skill.parent.mkdir(parents=True)
    legacy_skill.write_text("legacy skill\n")

    subprocess.run(
        ["bash", str(bundle / "install.sh")],
        env={
            "HOME": str(home),
            "PATH": "/usr/bin:/bin",
            "ENDNOTE_SAFE_WORD_DATA_HOME": str(data_home),
            "ENDNOTE_SAFE_WORD_BIN_HOME": str(bin_home),
            "CODEX_SKILLS_HOME": str(home / "skills"),
        },
        check=True,
        capture_output=True,
        text=True,
    )
    assert not legacy_link.exists()
    assert not legacy_skill.exists()
    backups = list((home / "skills/.backups").glob("endnote-safe-word-*/SKILL.md"))
    assert len(backups) == 1
    assert backups[0].read_text() == "legacy skill\n"
    assert (home / "skills/word-document-safe-editing/SKILL.md").read_text() == "renamed skill\n"
