"""Read source files, map them to the canonical model, pseudonymize, check health."""

from probative.ingest.files import SourceFile, read_table, read_tables
from probative.ingest.health import HealthIssue, HealthReport, check_work_units
from probative.ingest.mapping import PRESETS, MappingError, apply_mapping, resolve_mapping
from probative.ingest.pseudonymize import Pseudonymizer

__all__ = [
    "PRESETS",
    "HealthIssue",
    "HealthReport",
    "MappingError",
    "Pseudonymizer",
    "SourceFile",
    "apply_mapping",
    "check_work_units",
    "read_table",
    "read_tables",
    "resolve_mapping",
]
