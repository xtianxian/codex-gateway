from __future__ import annotations

import tomllib
from pathlib import Path


def test_pyproject_uses_codex_gateway_package_and_cli() -> None:
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert data["project"]["name"] == "codex-gateway"
    assert data["project"]["scripts"] == {
        "codex-gateway": "codex_gateway.__main__:main",
    }
    assert data["tool"]["setuptools"]["packages"]["find"]["where"] == ["src"]
    assert data["tool"]["setuptools"]["packages"]["find"]["include"] == ["codex_gateway*"]
    assert data["tool"]["setuptools"]["package-data"]["codex_gateway.backends.codex_app_server.protocol"] == [
        "*.json",
        "v1/*.json",
        "v2/*.json",
    ]
    assert data["tool"]["pytest"]["ini_options"]["pythonpath"] == ["src", "."]


def test_cli_parser_uses_codex_gateway_prog() -> None:
    from codex_gateway.__main__ import build_parser

    assert build_parser().prog == "codex-gateway"


def test_active_docs_use_uv_only_setup_commands() -> None:
    active_docs = [
        Path("AGENTS.md"),
        Path("CLAUDE.md"),
        Path("COMPATIBILITY.md"),
        Path("DOCUMENT.md"),
        Path("PLAN.md"),
        Path("README.md"),
        Path("RUNBOOK.md"),
    ]
    forbidden_fragments = [
        "py -3",
        "python -m",
        "python testing",
        "pip install",
        ".venv",
        ".\\.venv",
    ]

    hits: list[str] = []
    for path in active_docs:
        text = path.read_text(encoding="utf-8").lower()
        for fragment in forbidden_fragments:
            if fragment in text:
                hits.append(f"{path}: {fragment}")

    assert hits == []


def test_readme_documents_codex_cli_prerequisites() -> None:
    text = Path("README.md").read_text(encoding="utf-8")

    assert "Codex CLI installed and authenticated" in text
    assert "codex --version" in text


def test_docker_setup_supports_codex_access_token_auth() -> None:
    wrapper = Path("testing/docker/codex-gateway-docker").read_text(encoding="utf-8")
    compose = Path("testing/docker/compose.linux.yaml").read_text(encoding="utf-8")

    assert "CODEX_ACCESS_TOKEN" in wrapper
    assert "codex login --with-access-token" in wrapper
    assert "codex login --device-auth" in wrapper
    assert "CODEX_ACCESS_TOKEN: ${CODEX_ACCESS_TOKEN:-}" in compose

