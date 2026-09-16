from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

import endnote_safe_word

ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_and_package_version_match() -> None:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        metadata_version = tomllib.load(handle)["project"]["version"]
    assert metadata_version == endnote_safe_word.__version__


def test_version_is_pep440_parseable() -> None:
    from packaging.version import Version  # type: ignore[import-not-found]

    Version(endnote_safe_word.__version__)