"""Sample rows of approved Bronze tables for data analysis (FR-B11). Phase 0 has no Databricks access, so the only sampler says so.

A real connector implements RowSampler. It must run a read-only query capped at the limit it is given. The rows go back to the
IDE only, to be shown in the data_extraction stage: nothing here or in the tool writes them to disk.
"""

from typing import Any, Protocol

MAX_SAMPLE_ROWS = 20


class SamplingUnavailable(RuntimeError):
    pass


class RowSampler(Protocol):
    """Interface for a future source connector (for example Databricks). Not implemented in Phase 0."""

    def sample(self, table: str, limit: int) -> list[dict[str, Any]]: ...


class UnavailableSampler:
    def sample(self, table: str, limit: int) -> list[dict[str, Any]]:
        raise SamplingUnavailable("no source connector in Phase 0 (FR-B11 stub): sampling is not available, so data analysis stays metadata only")
