from __future__ import annotations

from traitlets import Unicode

from ipywidgets import Box


MODULE_NAME = "aipywidgets"
MODULE_VERSION = "*"


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
