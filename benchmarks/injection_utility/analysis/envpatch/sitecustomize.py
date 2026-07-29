"""In-memory override of vectr's proactive-injection envelope text.

Used ONLY by the injection-utility envelope A/B. Patches the module object
after import, in whatever process imports it, so the vectr source tree at
/Users/swapnanilsaha/dev/vectr is never modified (8765 runs editable off it).

Activate by putting this directory on PYTHONPATH and setting:
  VECTR_ENVELOPE_OPEN_OVERRIDE  replacement text for _ENVELOPE_OPEN
  VECTR_ENVELOPE_CLOSE_OVERRIDE replacement text for _ENVELOPE_CLOSE
An empty string is a valid override (renders a bare marker line).
"""
import os
import sys

GATE = "agent.proactive.gate"
MATCHER = "agent.proactive.matcher"
_OPEN = os.environ.get("VECTR_ENVELOPE_OPEN_OVERRIDE")
_CLOSE = os.environ.get("VECTR_ENVELOPE_CLOSE_OVERRIDE")
# Deliver the note BODY instead of matcher._raw_summary's title-only rendering.
_FULL_BODY = os.environ.get("VECTR_INJECT_FULL_BODY") == "1"


def _patch_matcher(module):
    def _raw_summary(note):
        content = (getattr(note, "content", "") or "").strip()
        if content:
            return " ".join(content.split())
        title = (getattr(note, "title", "") or "").strip()
        return title

    module._raw_summary = _raw_summary
    module._VECTR_FULL_BODY_PATCHED = True


class _EnvelopeShim:
    """Delegates to the real finder, then patches the module after exec."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in (GATE, MATCHER):
            return None
        if fullname == GATE and _OPEN is None and _CLOSE is None:
            return None
        if fullname == MATCHER and not _FULL_BODY:
            return None
        spec = None
        for finder in sys.meta_path:
            if finder is self:
                continue
            find = getattr(finder, "find_spec", None)
            if find is None:
                continue
            spec = find(fullname, path, target)
            if spec is not None:
                break
        if spec is None or spec.loader is None:
            return None

        original_exec = spec.loader.exec_module

        def exec_module(module, _orig=original_exec, _name=fullname):
            _orig(module)
            if _name == GATE:
                if _OPEN is not None:
                    module._ENVELOPE_OPEN = _OPEN
                if _CLOSE is not None:
                    module._ENVELOPE_CLOSE = _CLOSE
                module._VECTR_ENVELOPE_PATCHED = True
            elif _name == MATCHER:
                _patch_matcher(module)

        try:
            spec.loader.exec_module = exec_module
        except (AttributeError, TypeError):
            return None
        return spec


if _OPEN is not None or _CLOSE is not None or _FULL_BODY:
    sys.meta_path.insert(0, _EnvelopeShim())
