"""RAL-Farben im Freitext erkennen und exakt (statt vom LLM geschätzt) in Hex umrechnen."""

from __future__ import annotations

import re

from lumira_blv.logic.ral_table import RAL_CLASSIC

_RAL = re.compile(r"\bRAL[\s-]*(\d{4})\b", re.IGNORECASE)


def find_ral(text: str | None) -> tuple[str, str, str] | None:
    """Erster bekannter RAL-Classic-Code im Text → (Code, Hex, deutscher Name)."""
    for match in _RAL.finditer(text or ""):
        code = match.group(1)
        if code in RAL_CLASSIC:
            hex_value, name = RAL_CLASSIC[code]
            return code, hex_value, name
    return None
