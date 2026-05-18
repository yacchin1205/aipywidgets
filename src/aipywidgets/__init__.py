import sys
from .actions import Action
from .assist_layer import AssistLayer
from .ai import PatchOperation, PatchProposal, WhenIdle
from .config import AIConfig
from .form import AIForm
from . import fields

from pathlib import Path


def _jupyter_labextension_paths():
    labext_name = "aipywidgets"
    here = Path(__file__).parent.resolve()
    src_prefix = here.parent.parent / "labextension"
    if not src_prefix.exists():
        src_prefix = Path(sys.prefix) / f"share/jupyter/labextensions/{labext_name}"
    return [{"src": str(src_prefix), "dest": labext_name}]


__all__ = ["AIConfig", "AIForm", "Action", "AssistLayer", "PatchOperation", "PatchProposal", "WhenIdle", "fields"]
