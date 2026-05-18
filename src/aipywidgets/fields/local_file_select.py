from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any

import ipywidgets as widgets

from .base import Field


@dataclass(frozen=True)
class _TreeNode:
    name: str
    relative_path: str
    is_dir: bool
    children: tuple["_TreeNode", ...] = ()


class _SelectionProxy:
    def __init__(self, refresh: Callable[[list[str]], None]) -> None:
        self._refresh = refresh
        self._value: list[str] = []

    @property
    def value(self) -> list[str]:
        return list(self._value)

    @value.setter
    def value(self, value: list[str]) -> None:
        self._value = list(value)
        self._refresh(self._value)


@dataclass
class LocalFileSelect(Field):
    root_path: str = ""
    default: list[str] = field(default_factory=list)
    _expanded_dirs: set[str] = field(default_factory=set, init=False, repr=False)

    def empty_value(self) -> list[str]:
        return list(self.default)

    def validate_schema(self, validate_fields: Callable[[list["Field"], str], None], owner: str) -> None:
        root = self._root_directory()
        for relative_path in self.default:
            if not isinstance(relative_path, str) or not relative_path.strip():
                raise ValueError(f"LocalFileSelect default entries must be non-empty strings: {self.id}")
            candidate = root / relative_path.rstrip("/")
            if not candidate.exists():
                raise ValueError(f"LocalFileSelect default entry does not exist: {relative_path}")
            if candidate.is_dir() and not relative_path.endswith("/"):
                raise ValueError(f"LocalFileSelect directory entries must end with '/': {relative_path}")

    def render(self, form, path: str, allocation, grid):
        title = widgets.HTML(f"{self._css()}<strong>{escape(self.label or self.id)}</strong>")
        error_widget = widgets.HTML("")
        summary_widget = widgets.HTML("")
        toolbar = widgets.HBox([summary_widget], layout=widgets.Layout(width="100%", align_items="center"))
        tree_box = widgets.VBox([], layout=widgets.Layout(width="100%", overflow="visible"))
        form._register_field(path, self, error_widget)

        def refresh(selected_paths: list[str]) -> None:
            nodes = self._tree_nodes()
            selected = self._selected_paths(selected_paths)
            self._prune_expanded_dirs(nodes)
            self._expand_selected_ancestors(selected)
            summary_widget.value = self._summary_markup(selected)
            tree_box.children = tuple(self._render_node(form, path, node, selected, level=0) for node in nodes)

        proxy = _SelectionProxy(refresh)
        form._widgets[path] = proxy
        proxy.value = form.get_value(path)

        box = widgets.VBox(
            [title, error_widget, toolbar, tree_box],
            layout=widgets.Layout(width="100%", overflow="visible"),
        )
        if self.full_width:
            box.layout.width = "100%"
        return box

    def _css(self) -> str:
        return """
<style>
.aipy-local-file-select-caret,
.aipy-local-file-select-caret:hover,
.aipy-local-file-select-caret:focus,
.aipy-local-file-select-caret:active {
  background: transparent !important;
  background-color: transparent !important;
  box-shadow: none !important;
  outline: none !important;
  border-color: transparent !important;
}
</style>
"""

    def _root_directory(self) -> Path:
        if not self.root_path:
            raise ValueError(f"LocalFileSelect requires a root_path: {self.id}")
        root = Path(self.root_path)
        if not root.exists():
            raise ValueError(f"LocalFileSelect root_path does not exist: {self.root_path}")
        if not root.is_dir():
            raise ValueError(f"LocalFileSelect root_path must be a directory: {self.root_path}")
        return root

    def _tree_nodes(self) -> tuple[_TreeNode, ...]:
        root = self._root_directory()
        return self._directory_nodes(root, root)

    def _directory_nodes(self, root: Path, directory: Path) -> tuple[_TreeNode, ...]:
        nodes = []
        entries = sorted(directory.iterdir(), key=lambda candidate: (candidate.is_file(), candidate.name.lower()))
        for entry in entries:
            relative_path = entry.relative_to(root).as_posix()
            if entry.is_dir():
                nodes.append(
                    _TreeNode(
                        name=entry.name,
                        relative_path=f"{relative_path}/",
                        is_dir=True,
                        children=self._directory_nodes(root, entry),
                    )
                )
                continue
            nodes.append(_TreeNode(name=entry.name, relative_path=relative_path, is_dir=False))
        return tuple(nodes)

    def _selected_paths(self, selected_paths: list[str]) -> set[str]:
        if not isinstance(selected_paths, list):
            raise TypeError(f"LocalFileSelect value must be a list: {self.id}")
        selected = set()
        for relative_path in selected_paths:
            if not isinstance(relative_path, str) or not relative_path.strip():
                raise TypeError(f"LocalFileSelect entries must be non-empty strings: {self.id}")
            selected.add(relative_path)
        return selected

    def _render_node(self, form, path: str, node: _TreeNode, selected: set[str], *, level: int):
        if node.is_dir:
            return self._render_directory_node(form, path, node, selected, level=level)
        return self._render_file_node(form, path, node, selected, level=level)

    def _render_directory_node(self, form, path: str, node: _TreeNode, selected: set[str], *, level: int):
        expanded = node.relative_path in self._expanded_dirs
        caret = widgets.Button(
            description="",
            icon="caret-down" if expanded else "caret-right",
            layout=widgets.Layout(width="20px", min_width="20px", padding="0px"),
        )
        caret.style.button_color = "#ffffff"
        caret.add_class("aipy-local-file-select-caret")
        checkbox = widgets.Checkbox(
            description="",
            value=node.relative_path in selected,
            indent=False,
            layout=widgets.Layout(width="18px", min_width="18px"),
        )
        checkbox.tooltip = f"Select {node.relative_path}"
        checkbox.observe(
            lambda change, rel=node.relative_path: self._on_checkbox_change(form, path, rel, change),
            names="value",
        )
        icon = self._icon_widget(is_dir=True, expanded=expanded)
        label = widgets.HTML(
            f"<span style='font-weight: 600;'>{escape(node.name)}</span>"
            f"<span style='color: #6e7781; margin-left: 8px;'>{self._directory_suffix(node, selected)}</span>"
        )
        row = widgets.HBox(
            [self._indent(level), caret, checkbox, icon, label],
            layout=widgets.Layout(width="100%", align_items="center"),
        )

        def toggle(_button) -> None:
            if node.relative_path in self._expanded_dirs:
                self._expanded_dirs.remove(node.relative_path)
            else:
                self._expanded_dirs.add(node.relative_path)
            form._widgets[path].value = form.get_value(path)

        caret.on_click(toggle)
        children = []
        if expanded:
            children = [self._render_node(form, path, child, selected, level=level + 1) for child in node.children]
        return widgets.VBox([row, *children], layout=widgets.Layout(width="100%", overflow="visible"))

    def _render_file_node(self, form, path: str, node: _TreeNode, selected: set[str], *, level: int):
        checkbox = widgets.Checkbox(
            description="",
            value=node.relative_path in selected,
            indent=False,
            layout=widgets.Layout(width="24px", min_width="24px"),
        )
        checkbox.tooltip = f"Select {node.relative_path}"
        checkbox.observe(
            lambda change, rel=node.relative_path: self._on_checkbox_change(form, path, rel, change),
            names="value",
        )
        spacer = widgets.HTML("", layout=widgets.Layout(width="20px", min_width="20px"))
        icon = self._icon_widget(is_dir=False, expanded=False)
        label = widgets.HTML(f"<span>{escape(node.name)}</span>")
        row = widgets.HBox(
            [self._indent(level), spacer, checkbox, icon, label],
            layout=widgets.Layout(width="100%", align_items="center"),
        )
        return row

    def _indent(self, level: int):
        return widgets.HTML("", layout=widgets.Layout(width=f"{level * 20}px", min_width=f"{level * 20}px"))

    def _directory_suffix(self, node: _TreeNode, selected: set[str]) -> str:
        file_count = self._descendant_file_count(node)
        selected_count = self._selected_descendant_count(node, selected)
        selected_label = " selected," if node.relative_path in selected else ""
        if selected_count == 0:
            return f"{selected_label} {file_count} files".strip()
        return f"{selected_label} {selected_count} selected / {file_count} files".strip()

    def _icon_markup(self, *, is_dir: bool, expanded: bool) -> str:
        icon = "fa-folder-open-o" if is_dir and expanded else "fa-folder-o" if is_dir else "fa-file-o"
        color = "#8c6d1f" if is_dir else "#6e7781"
        return f"<i class='fa {icon}' style='color: {color}; width: 14px; text-align: center;'></i>"

    def _icon_widget(self, *, is_dir: bool, expanded: bool):
        widget = widgets.HTML(
            self._icon_markup(is_dir=is_dir, expanded=expanded),
            layout=widgets.Layout(width="16px", min_width="16px"),
        )
        widget.layout.margin = "0 2px 0 -2px" if is_dir else "0 2px 0 -8px"
        return widget

    def _descendant_file_count(self, node: _TreeNode) -> int:
        if not node.is_dir:
            return 1
        return sum(self._descendant_file_count(child) for child in node.children)

    def _selected_descendant_count(self, node: _TreeNode, selected: set[str]) -> int:
        if not node.is_dir:
            return 1 if node.relative_path in selected else 0
        return sum(self._selected_descendant_count(child, selected) for child in node.children)

    def _summary_markup(self, selected: set[str]) -> str:
        if not selected:
            return "<span style='color: #6e7781;'>No items selected</span>"
        preview = ", ".join(f"<code>{escape(path)}</code>" for path in sorted(selected)[:3])
        hidden_count = len(selected) - min(len(selected), 3)
        suffix = "" if hidden_count == 0 else f" and {hidden_count} more"
        noun = "item" if len(selected) == 1 else "items"
        return f"<span><strong>{len(selected)}</strong> {noun} selected: {preview}{escape(suffix)}</span>"

    def _all_directory_paths(self, nodes: tuple[_TreeNode, ...]) -> set[str]:
        paths: set[str] = set()
        for node in nodes:
            if not node.is_dir:
                continue
            paths.add(node.relative_path)
            paths.update(self._all_directory_paths(node.children))
        return paths

    def _prune_expanded_dirs(self, nodes: tuple[_TreeNode, ...]) -> None:
        valid_paths = self._all_directory_paths(nodes)
        self._expanded_dirs.intersection_update(valid_paths)

    def _expand_selected_ancestors(self, selected: set[str]) -> None:
        for relative_path in selected:
            if relative_path.endswith("/"):
                self._expanded_dirs.add(relative_path)
            path = Path(relative_path.rstrip("/"))
            for parent in path.parents:
                if str(parent) == ".":
                    continue
                self._expanded_dirs.add(f"{parent.as_posix()}/")

    def _on_checkbox_change(self, form, path: str, relative_path: str, change: dict[str, Any]) -> None:
        if change["name"] != "value":
            raise ValueError(f"Unexpected LocalFileSelect change event for {path}: {change!r}")
        selected = self._selected_paths(form.get_value(path))
        if change["new"]:
            selected.add(relative_path)
        else:
            selected.discard(relative_path)
        form.set_value(path, sorted(selected))
