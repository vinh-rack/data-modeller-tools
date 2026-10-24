"""Redshift metadata connector (stub). Will read table and column metadata with a read-only user."""

from dma.connectors.base import MetadataConnector


class RedshiftMetadata(MetadataConnector):
    name = "redshift"
    env_vars = ("REDSHIFT_HOST", "REDSHIFT_PORT", "REDSHIFT_DATABASE", "REDSHIFT_USER", "REDSHIFT_PASSWORD")

    def list_columns(self, schema: str, table: str) -> list[dict]:
        # TODO(v1): verify the current redshift_connector API before implementing. Metadata queries only.
        raise self._not_ready()
