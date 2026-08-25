"""Path setup, plus the fixtures every test module may need.

Makes ``from helpers import ...`` work whatever import mode pytest is run in —
the ``sys.path.insert`` below is the mechanism. Without it the import resolves
only under the default ``prepend`` mode (which happens to put this directory on
``sys.path``); under ``--import-mode=importlib``, or with a ``tests/__init__.py``,
the failure would be an ImportError at collection — the whole suite, not one test.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


import pytest
import yaml
from helpers import UNSERVABLE_BUNDLE


@pytest.fixture
def unservable_persona(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Install :data:`helpers.UNSERVABLE_BUNDLE` as the only persona on disk.

    Lives here rather than in ``helpers.py`` so pytest discovers it for every module —
    ``test_media.py`` and ``test_webhooks.py`` both had a verbatim copy of it.
    """
    directory = tmp_path / "unvoiceable"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "core.md").write_text("You are a test persona.\n", encoding="utf-8")
    (directory / "persona.yaml").write_text(yaml.safe_dump(UNSERVABLE_BUNDLE), encoding="utf-8")
    monkeypatch.setattr("ssscammers.agent.persona.PERSONA_DIR", tmp_path)
    return "unvoiceable"
