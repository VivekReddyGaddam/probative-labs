"""Team-level reporting with a minimum group size.

Probative Labs never scores individuals. Teams with fewer distinct people than the
minimum group size are left out of the analysis entirely, so no result can be
traced back to one person.
"""

from __future__ import annotations

import pandas as pd

from probative import schema as S


def team_sizes(units: pd.DataFrame) -> pd.Series | None:
    """Distinct (pseudonymized) assignees per team, or ``None`` if assignees are not recorded."""
    if S.ASSIGNEE not in units.columns or units[S.ASSIGNEE].isna().all():
        return None
    return units.dropna(subset=[S.ASSIGNEE]).groupby(S.TEAM)[S.ASSIGNEE].nunique()


def small_teams(sizes: pd.Series | None, min_group_size: int) -> list[str]:
    if sizes is None:
        return []
    return sorted(str(t) for t in sizes[sizes < min_group_size].index)
