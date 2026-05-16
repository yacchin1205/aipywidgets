from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PathPart:
    name: str | None = None
    index: int | str | None = None


def parse_field_path(path: str) -> list[PathPart]:
    if path in {"", "."}:
        return []
    if path.startswith("./"):
        path = path[2:]

    parts: list[PathPart] = []
    for chunk in path.split("."):
        if not chunk:
            raise ValueError(f"Invalid field path: {path!r}")
        if "[" not in chunk:
            parts.append(PathPart(name=chunk))
            continue
        name, rest = chunk.split("[", 1)
        if not rest.endswith("]"):
            raise ValueError(f"Invalid field path: {path!r}")
        raw_index = rest[:-1]
        if raw_index == "*":
            index: int | str = "*"
        else:
            index = int(raw_index)
        if name:
            parts.append(PathPart(name=name))
        parts.append(PathPart(index=index))
    return parts


def get_in(data: object, path: str) -> object:
    current = data
    for part in parse_field_path(path):
        if part.name is not None:
            current = current[part.name]  # type: ignore[index]
        elif part.index is not None:
            if part.index == "*":
                raise ValueError("Wildcard paths cannot resolve to one value")
            current = current[part.index]  # type: ignore[index]
    return current


def set_in(data: object, path: str, value: object) -> None:
    parts = parse_field_path(path)
    if not parts:
        raise ValueError("Cannot replace the form root with set_value")
    current = data
    for part in parts[:-1]:
        if part.name is not None:
            current = current[part.name]  # type: ignore[index]
        elif part.index is not None:
            if part.index == "*":
                raise ValueError("Wildcard paths cannot be used for direct writes")
            current = current[part.index]  # type: ignore[index]

    last = parts[-1]
    if last.name is not None:
        current[last.name] = value  # type: ignore[index]
    elif last.index is not None:
        if last.index == "*":
            raise ValueError("Wildcard paths cannot be used for direct writes")
        current[last.index] = value  # type: ignore[index]
