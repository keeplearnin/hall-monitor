"""Matching people's names: case, accents, punctuation and spacing don't count."""

from __future__ import annotations

import re
import unicodedata


def name_key(name: str) -> str:
    """The form two names are compared in: "José  García." and "JOSE GARCIA" match. Nicknames
    and initials ("Dan" for "Daniel") don't."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]", " ", plain.casefold()).split())
