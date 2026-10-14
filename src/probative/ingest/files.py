"""CSV and Parquet readers that record lineage for every file they touch."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class SourceFile:
    path: str
    sha256: str
    rows: int
    columns: tuple[str, ...]
    modified: str

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "rows": self.rows,
            "columns": list(self.columns),
            "modified": self.modified,
        }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_table(path: str | Path) -> tuple[pd.DataFrame, SourceFile]:
    """Read a CSV or Parquet file as strings; typing happens in the mapping step."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"data file not found: {path}")
    suffix = path.suffix.lower()
    if suffix in {".parquet", ".pq"}:
        try:
            df = pd.read_parquet(path)
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ImportError("Parquet support needs: pip install 'probative[parquet]'") from exc
    elif suffix in {".csv", ".txt"}:
        df = pd.read_csv(path, dtype=str, keep_default_na=True)
    else:
        raise ValueError(f"unsupported file type {suffix!r}; use CSV or Parquet")
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat(timespec="seconds")
    meta = SourceFile(
        path=str(path),
        sha256=_sha256(path),
        rows=len(df),
        columns=tuple(str(c) for c in df.columns),
        modified=modified,
    )
    return df, meta


def read_tables(paths: list[Path]) -> tuple[pd.DataFrame, list[SourceFile], list[str]]:
    """Read and stack several extracts of the same table.

    Returns the combined frame, per-file lineage and warnings about schema
    differences between files (a schema change mid-history is a common source of
    false effects).
    """
    frames, metas, warnings = [], [], []
    for p in paths:
        df, meta = read_table(p)
        frames.append(df)
        metas.append(meta)
    if len(metas) > 1:
        base = set(metas[0].columns)
        for meta in metas[1:]:
            cols = set(meta.columns)
            if cols != base:
                added, removed = sorted(cols - base), sorted(base - cols)
                warnings.append(
                    f"schema differs in {Path(meta.path).name}: added {added or 'none'}, removed {removed or 'none'}"
                )
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return combined, metas, warnings
