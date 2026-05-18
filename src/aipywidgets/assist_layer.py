from __future__ import annotations

import json
import sys
from importlib.metadata import version as package_version
from pathlib import Path
from traitlets import Unicode

from ipywidgets import Box


MODULE_NAME = "aipywidgets"


def _load_package_json_version() -> str | None:
    for parent in Path(__file__).resolve().parents:
        package_json = parent / "package.json"
        if package_json.exists():
            data = json.loads(package_json.read_text())
            return data.get("version")
    installed_package = Path(sys.prefix) / "share" / "jupyter" / "labextensions" / MODULE_NAME / "package.json"
    if installed_package.exists():
        data = json.loads(installed_package.read_text())
        return data.get("version")
    return None


MODULE_VERSION = _load_package_json_version() or package_version(MODULE_NAME)


class AssistLayer(Box):
    _model_name = Unicode("AssistLayerModel").tag(sync=True)
    _view_name = Unicode("AssistLayerView").tag(sync=True)
    _model_module = Unicode(MODULE_NAME).tag(sync=True)
    _view_module = Unicode(MODULE_NAME).tag(sync=True)
    _model_module_version = Unicode(MODULE_VERSION).tag(sync=True)
    _view_module_version = Unicode(MODULE_VERSION).tag(sync=True)

    form_dom_class = Unicode("").tag(sync=True)
    anchor_dom_class = Unicode("").tag(sync=True)
    placement = Unicode("right").tag(sync=True)
