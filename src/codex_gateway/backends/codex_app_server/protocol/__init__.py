from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


SCHEMA_BUNDLE = Path(__file__).with_name("codex_app_server_protocol.v2.schemas.json")
SERVER_REQUEST_SCHEMA = Path(__file__).with_name("ServerRequest.json")
GENERATED_WITH = "codex-cli 0.133.0"
GENERATION_COMMAND = (
    "codex app-server generate-json-schema "
    "--out src\\codex_gateway\\backends\\codex_app_server\\protocol --experimental"
)


@lru_cache(maxsize=1)
def generated_protocol_methods(schema_bundle: str | Path = SCHEMA_BUNDLE) -> frozenset[str]:
    data = json.loads(Path(schema_bundle).read_text(encoding="utf-8"))
    methods: set[str] = set()
    _collect_method_literals(data, methods)
    return frozenset(methods)


@lru_cache(maxsize=1)
def generated_server_request_methods(schema_file: str | Path = SERVER_REQUEST_SCHEMA) -> frozenset[str]:
    data = json.loads(Path(schema_file).read_text(encoding="utf-8"))
    methods: set[str] = set()
    for variant in data.get("oneOf") or []:
        if not isinstance(variant, dict):
            continue
        method = ((variant.get("properties") or {}).get("method") or {})
        if not isinstance(method, dict):
            continue
        values = method.get("enum")
        if isinstance(values, list):
            methods.update(item for item in values if isinstance(item, str))
        const = method.get("const")
        if isinstance(const, str):
            methods.add(const)
    return frozenset(methods)


def _collect_method_literals(value: Any, methods: set[str]) -> None:
    if isinstance(value, dict):
        for key in ("const", "default"):
            item = value.get(key)
            if _looks_like_method(item):
                methods.add(item)
        enum = value.get("enum")
        if isinstance(enum, list):
            methods.update(item for item in enum if _looks_like_method(item))
        for item in value.values():
            _collect_method_literals(item, methods)
    elif isinstance(value, list):
        for item in value:
            _collect_method_literals(item, methods)


def _looks_like_method(value: Any) -> bool:
    return isinstance(value, str) and "/" in value and not value.startswith("#/")
