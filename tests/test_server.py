import os
import subprocess
import sys
from unittest.mock import patch


def test_parse_args_defaults_code_mode_off():
    from owui_mcp.server import parse_args

    args = parse_args([])
    assert args.code_mode is False


def test_parse_args_enables_code_mode():
    from owui_mcp.server import parse_args

    args = parse_args(["--code-mode"])
    assert args.code_mode is True


def test_create_server_without_code_mode_has_no_transforms():
    from owui_mcp.server import create_server

    with patch("owui_mcp.server.OpenWebUI"):
        mcp = create_server(code_mode=False)

    transforms = getattr(mcp, "_transforms", getattr(mcp, "transforms", []))
    assert not any(t.__class__.__name__ == "CodeMode" for t in transforms)


def test_create_server_with_code_mode_has_transform():
    from fastmcp.experimental.transforms.code_mode import CodeMode
    from owui_mcp.server import create_server

    with patch("owui_mcp.server.OpenWebUI"):
        mcp = create_server(code_mode=True)

    transforms = getattr(mcp, "_transforms", getattr(mcp, "transforms", []))
    assert any(isinstance(t, CodeMode) for t in transforms)


def test_help_works_without_env_vars():
    env = {k: v for k, v in os.environ.items() if k not in ("OWUI_API_URL", "OWUI_API_KEY")}
    result = subprocess.run(
        [sys.executable, "-m", "owui_mcp", "--help"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0
    assert "--code-mode" in result.stdout
