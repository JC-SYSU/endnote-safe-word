from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .patcher import PatchSpec, patch_docx
from .scanner import scan_docx
from .verifier import verify_docx


def _build_server():
    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.server.fastmcp.server import Settings
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "MCP support is optional. Install the package with its 'mcp' extra."
        ) from exc

    # MCP 1.29 leaves Settings.lifespan as a forward reference. Rebuild the
    # upstream model before pydantic-settings inspects it; do not hide the
    # resulting compatibility warning with a warning filter.
    Settings.model_rebuild()
    mcp = FastMCP("word-document-safe-editing")

    @mcp.tool()
    def scan_endnote_docx(path: str) -> str:
        """Scan a DOCX and return EndNote field and formatting invariants as JSON."""
        return json.dumps(scan_docx(path).to_dict(), ensure_ascii=False, indent=2)

    @mcp.tool()
    def verify_endnote_docx(before_path: str, after_path: str) -> str:
        """Verify that EndNote fields, Word field markers, and super/subscripts survived."""
        return json.dumps(
            verify_docx(before_path, after_path), ensure_ascii=False, indent=2
        )

    @mcp.tool()
    def patch_endnote_docx(
        input_path: str,
        output_path: str,
        patches: list[dict[str, Any]],
    ) -> str:
        """Apply conservative single-w:t replacements outside fields and revisions."""
        specs = [PatchSpec.from_dict(item) for item in patches]
        result = patch_docx(input_path, output_path, specs)
        return json.dumps(result, ensure_ascii=False, indent=2)

    return mcp


def main() -> None:
    _build_server().run()


if __name__ == "__main__":
    main()
