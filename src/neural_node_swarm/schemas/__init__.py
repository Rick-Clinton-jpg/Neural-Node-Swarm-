"""JSON schemas shipped with the package.

These files are byte-identical copies of the repository's ``schemas/`` directory
(enforced by ``tests/test_packaged_schemas.py``), so an installed build can verify
node outputs without a source checkout.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any


def load_schema(name: str) -> dict[str, Any]:
    """Load ``<name>.schema.json`` from the packaged schema copies."""
    text = resources.files(__name__).joinpath(f"{name}.schema.json").read_text(encoding="utf-8")
    return json.loads(text)
