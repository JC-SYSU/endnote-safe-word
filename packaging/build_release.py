#!/usr/bin/env python3
"""Build a source-independent EndNote Safe Word release bundle."""

from __future__ import annotations

import argparse
import ast
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 release-maintainer fallback
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = Path(__file__).resolve().parent / "templates"


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        metadata_version = tomllib.load(handle)["project"]["version"]

    tree = ast.parse((ROOT / "src/endnote_safe_word/__init__.py").read_text())
    code_version: str | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        ):
            code_version = ast.literal_eval(node.value)
            break
    if code_version != metadata_version:
        raise RuntimeError(
            f"Version mismatch: pyproject.toml={metadata_version!r}, "
            f"__version__={code_version!r}"
        )
    return metadata_version


def source_date_epoch() -> str:
    configured = os.environ.get("SOURCE_DATE_EPOCH")
    if configured:
        return configured
    result = subprocess.run(
        ["git", "log", "-1", "--format=%ct"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or "0"


def build_wheel(destination: Path) -> Path:
    environment = os.environ.copy()
    environment["SOURCE_DATE_EPOCH"] = source_date_epoch()
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(destination)],
        cwd=ROOT,
        env=environment,
        check=True,
    )
    wheels = list(destination.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"Expected one wheel, found {len(wheels)}")
    return wheels[0]


def validate_wheel(wheel: Path) -> None:
    package_root = ROOT / "src/endnote_safe_word"
    expected = {
        path.relative_to(ROOT / "src").as_posix()
        for path in package_root.rglob("*")
        if path.is_file()
        and (path.suffix == ".py" or "policies" in path.relative_to(package_root).parts)
    }
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    missing = sorted(expected - names)
    forbidden = sorted(
        name
        for name in names
        if name.startswith(("tests/", "build/", "docs/"))
        or "/__pycache__/" in name
        or name.endswith((".pyc", ".DS_Store"))
    )
    if missing or forbidden:
        details = []
        if missing:
            details.append(f"missing files: {missing}")
        if forbidden:
            details.append(f"forbidden files: {forbidden}")
        raise RuntimeError("Invalid wheel: " + "; ".join(details))


def copy_release_files(bundle: Path, wheel: Path, version: str) -> None:
    (bundle / "wheels").mkdir(parents=True)
    shutil.copy2(wheel, bundle / "wheels" / wheel.name)
    shutil.copytree(
        ROOT / "skill/word-document-safe-editing",
        bundle / "skill/word-document-safe-editing",
        ignore=shutil.ignore_patterns(".DS_Store", "__pycache__", "*.pyc"),
    )
    shutil.copy2(ROOT / "LICENSE", bundle / "LICENSE")
    shutil.copy2(TEMPLATES / "install.sh", bundle / "install.sh")
    (bundle / "install.sh").chmod(0o755)
    readme = (TEMPLATES / "README.md").read_text(encoding="utf-8")
    (bundle / "README.md").write_text(
        readme.replace("@VERSION@", version), encoding="utf-8"
    )
    (bundle / "VERSION").write_text(version + "\n", encoding="utf-8")


def write_checksums(bundle: Path) -> None:
    entries: list[str] = []
    for path in sorted(bundle.rglob("*")):
        if not path.is_file() or path.name == "SHA256SUMS":
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append(f"{digest}  {path.relative_to(bundle).as_posix()}")
    (bundle / "SHA256SUMS").write_text("\n".join(entries) + "\n", encoding="utf-8")


def archive_bundle(bundle: Path, archive: Path, epoch: int) -> None:
    with tarfile.open(archive, "w:gz", format=tarfile.PAX_FORMAT) as output:
        for path in [bundle, *sorted(bundle.rglob("*"))]:
            info = output.gettarinfo(path, arcname=path.relative_to(bundle.parent))
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = epoch
            if path.is_file():
                with path.open("rb") as handle:
                    output.addfile(info, handle)
            else:
                output.addfile(info)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "release",
        help="Output directory (default: repository release/)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    version = project_version()
    output = args.output.expanduser().resolve()
    bundle_name = f"endnote-safe-word-{version}"
    final_bundle = output / bundle_name
    final_archive = output / f"{bundle_name}.tar.gz"
    if final_bundle.exists() or final_archive.exists():
        raise RuntimeError(
            f"Release output already exists for {version}; remove it explicitly first"
        )

    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="endnote-safe-word-release-") as temp:
        temp_root = Path(temp)
        wheel = build_wheel(temp_root / "wheel")
        validate_wheel(wheel)
        staged_bundle = temp_root / bundle_name
        copy_release_files(staged_bundle, wheel, version)
        write_checksums(staged_bundle)
        staged_archive = temp_root / final_archive.name
        archive_bundle(staged_bundle, staged_archive, int(source_date_epoch()))
        shutil.move(staged_bundle, final_bundle)
        shutil.move(staged_archive, final_archive)

    print(f"Release bundle: {final_bundle}")
    print(f"Release archive: {final_archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
