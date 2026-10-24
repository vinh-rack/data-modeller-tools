"""Databricks metadata connector (stub). Will read Unity Catalog table and column metadata with a read-only token."""

from dma.connectors.base import MetadataConnector


class DatabricksMetadata(MetadataConnector):
    name = "databricks"
    env_vars = ("DATABRICKS_HOST", "DATABRICKS_HTTP_PATH", "DATABRICKS_TOKEN")

    def list_columns(self, catalog: str, schema: str, table: str) -> list[dict]:
        # TODO(v1): verify the current databricks-sdk or databricks-sql-connector API before implementing. Metadata only.
        raise self._not_ready()
