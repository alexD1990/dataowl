from __future__ import annotations

import subprocess
import sys

import pytest

_SCRIPT = """
import importlib
import pkgutil
import sys

sys.modules["pyspark"] = None

module = importlib.import_module({name!r})
for info in pkgutil.walk_packages(getattr(module, "__path__", []), module.__name__ + "."):
    importlib.import_module(info.name)
"""


@pytest.mark.parametrize(
    "name", ["dataowl", "dataowl.collect", "dataowl.model", "dataowl.render", "dataowl.runner"]
)
def test_importable_without_pyspark(name: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT.format(name=name)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
