"""Helpers that turn the OpenAPI schema into concrete requests.

Used by the authorization sweeps in this directory. Nothing here asserts; it
only builds requests, so every sweep can state its own expectation.

A request is built from the schema, not from a hand-written list, so a route
added tomorrow is swept tomorrow without anyone remembering to add it.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Optional

from fastapi import FastAPI

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
MUTATING = frozenset({"post", "put", "patch", "delete"})
_PATH_PARAM = re.compile(r"\{([^}]+)\}")


@dataclass(frozen=True)
class Op:
    """One (method, path) pair from the OpenAPI schema."""

    method: str
    path: str
    spec: Mapping[str, Any]
    params: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)

    @property
    def key(self) -> str:
        return f"{self.method.upper()} {self.path}"

    @property
    def path_params(self) -> list[str]:
        return _PATH_PARAM.findall(self.path)

    @property
    def is_mutating(self) -> bool:
        return self.method in MUTATING


class Sweep:
    """Builds requests for every operation in an app's OpenAPI schema."""

    def __init__(self, app: FastAPI) -> None:
        self.schema = app.openapi()
        self.components = self.schema.get("components", {}).get("schemas", {})

    # -- enumeration -----------------------------------------------------

    def operations(self) -> Iterator[Op]:
        for path, item in sorted(self.schema["paths"].items()):
            shared = tuple(item.get("parameters", []))
            for method in HTTP_METHODS:
                spec = item.get(method)
                if spec is None:
                    continue
                params = shared + tuple(spec.get("parameters", []))
                yield Op(method=method, path=path, spec=spec, params=params)

    # -- request construction ---------------------------------------------

    def build(
        self, op: Op, ids: Mapping[str, str]
    ) -> tuple[str, dict[str, Any], Optional[dict[str, Any]]]:
        """(url, query params, json body) with `ids` filling path parameters.

        Path parameters not in `ids` get a fresh random UUID (or a plausible
        string for non-UUID ones), so a sub-resource lookup finds nothing and
        the request is decided by the authorization layer alone.
        """
        url = op.path
        for name in op.path_params:
            url = url.replace("{" + name + "}", str(ids.get(name) or self._path_value(op, name)))

        query: dict[str, Any] = {}
        for param in op.params:
            if param.get("in") == "query" and param.get("required"):
                query[param["name"]] = self.example(param.get("schema", {}))

        body = None
        request_body = op.spec.get("requestBody")
        if request_body:
            content = request_body.get("content", {})
            media = content.get("application/json")
            if media is not None:
                body = self.example(media.get("schema", {}))
        return url, query, body

    def _path_value(self, op: Op, name: str) -> Any:
        for param in op.params:
            if param.get("in") == "path" and param.get("name") == name:
                schema = self._resolve(param.get("schema", {}))
                if schema.get("enum"):
                    return schema["enum"][0]
                for option in schema.get("anyOf", []) + schema.get("oneOf", []):
                    option = self._resolve(option)
                    if option.get("enum"):
                        return option["enum"][0]
                if schema.get("type") == "integer":
                    return 1
                if schema.get("format") == "uuid" or name.endswith("_id") or name == "id":
                    return uuid.uuid4()
                return "sweep-" + uuid.uuid4().hex[:8]
        return uuid.uuid4()

    # -- JSON-schema example generator ------------------------------------

    def _resolve(self, schema: Mapping[str, Any]) -> Mapping[str, Any]:
        seen = 0
        while "$ref" in schema and seen < 20:
            schema = self.components.get(schema["$ref"].rsplit("/", 1)[-1], {})
            seen += 1
        return schema

    def example(self, schema: Mapping[str, Any], depth: int = 0) -> Any:
        schema = self._resolve(schema)
        if depth > 6:
            return None
        if "const" in schema:
            return schema["const"]
        if schema.get("enum"):
            return schema["enum"][0]
        if "default" in schema and schema["default"] is not None:
            return schema["default"]
        for key in ("allOf", "anyOf", "oneOf"):
            if schema.get(key):
                options = [
                    option
                    for option in schema[key]
                    if self._resolve(option).get("type") != "null"
                ]
                if key == "allOf":
                    merged: dict[str, Any] = {}
                    for option in options:
                        piece = self.example(option, depth + 1)
                        if isinstance(piece, dict):
                            merged.update(piece)
                    return merged
                return self.example(options[0], depth + 1) if options else None

        kind = schema.get("type")
        if kind == "object" or "properties" in schema:
            required = set(schema.get("required", []))
            out: dict[str, Any] = {}
            for name, sub in (schema.get("properties") or {}).items():
                resolved = self._resolve(sub)
                is_tag = "const" in resolved or len(resolved.get("enum") or []) == 1
                # A discriminator ("kind": "snowflake") is what lets a union
                # body validate at all, even though it is not "required".
                if name in required or is_tag:
                    out[name] = self.example(sub, depth + 1)
            return out
        if kind == "array":
            items = schema.get("items", {})
            count = max(1, int(schema.get("minItems", 1) or 1))
            return [self.example(items, depth + 1) for _ in range(min(count, 2))]
        if kind == "integer":
            return int(schema.get("minimum", schema.get("exclusiveMinimum", 0) + 1 if "exclusiveMinimum" in schema else 1))
        if kind == "number":
            return float(schema.get("minimum", 1))
        if kind == "boolean":
            return False
        if kind == "string" or kind is None:
            fmt = schema.get("format")
            if fmt == "uuid":
                return str(uuid.uuid4())
            if fmt == "email":
                return "sweep@example.com"
            if fmt == "date-time":
                return "2026-01-01T00:00:00Z"
            if fmt == "date":
                return "2026-01-01"
            if fmt in ("uri", "url"):
                return "https://example.com/sweep"
            length = max(int(schema.get("minLength", 1) or 1), 1)
            return ("sweep" * length)[: max(length, 5)]
        return None
