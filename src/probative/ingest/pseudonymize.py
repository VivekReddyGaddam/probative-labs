"""Keyed hashing of person and record identifiers at ingest.

Names and user IDs are replaced with HMAC-SHA256 digests before storage. The
same key gives the same pseudonym, so joins (e.g. a user's follow-up ticket after
a self-service session) still work; without the key the mapping cannot be
reversed or recomputed from a list of employee IDs.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from collections.abc import Iterable
from pathlib import Path

import pandas as pd

ENV_KEY = "PROBATIVE_PSEUDONYM_KEY"


class Pseudonymizer:
    def __init__(self, key: bytes, digest_chars: int = 20):
        if len(key) < 16:
            raise ValueError("pseudonymization key must be at least 16 bytes")
        self._key = key
        self._chars = digest_chars

    @classmethod
    def from_env_or_file(cls, key_file: str | Path) -> Pseudonymizer:
        """Use ``PROBATIVE_PSEUDONYM_KEY`` (hex) if set, otherwise a key file created on first use."""
        env = os.environ.get(ENV_KEY)
        if env:
            return cls(bytes.fromhex(env))
        path = Path(key_file)
        if path.exists():
            return cls(bytes.fromhex(path.read_text().strip()))
        path.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        path.write_text(key.hex())
        path.chmod(0o600)
        return cls(key)

    def hash(self, value: object) -> str | None:
        if value is None or (isinstance(value, float) and value != value) or value is pd.NA:
            return None
        text = str(value).strip()
        if not text:
            return None
        digest = hmac.new(self._key, text.encode("utf-8"), hashlib.sha256).hexdigest()
        return "p_" + digest[: self._chars]

    def apply(self, df: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
        out = df.copy()
        for col in columns:
            if col in out.columns:
                uniques = out[col].dropna().unique()
                lookup = {u: self.hash(u) for u in uniques}
                out[col] = out[col].map(lookup).astype("string")
        return out
