import warnings

from word_document_safe_editing.mcp_server import _build_server


def test_supported_mcp_sdk_builds_server_without_warnings() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        server = _build_server()
    assert server.name == "word-document-safe-editing"
