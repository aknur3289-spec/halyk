"""Small, side-effect-safe utilities shared by pipeline components."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4


class JsonFileError(ValueError):
    """Raised when a JSON file cannot be read or persisted safely."""


def read_json_object(path: Path) -> dict[str, Any]:
    """Read a UTF-8 JSON file and require an object at its root."""

    try:
        with path.open("r", encoding="utf-8") as file:
            value = json.load(file)
    except FileNotFoundError as exc:
        raise JsonFileError(f"JSON file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise JsonFileError(f"Invalid JSON in {path}: {exc.msg}") from exc
    except OSError as exc:
        raise JsonFileError(f"Unable to read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise JsonFileError(f"JSON root must be an object: {path}")
    return value


def write_json_atomically(path: Path, document: dict[str, Any]) -> None:
    """Persist a JSON object using same-directory replacement to avoid partial files."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary_path.open("w", encoding="utf-8") as file:
            json.dump(document, file, ensure_ascii=False, indent=2, allow_nan=False)
            file.write("\n")
        temporary_path.replace(path)
    except (OSError, TypeError, ValueError) as exc:
        temporary_path.unlink(missing_ok=True)
        raise JsonFileError(f"Unable to write valid JSON to {path}: {exc}") from exc
