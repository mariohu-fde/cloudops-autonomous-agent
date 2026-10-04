"""Zero-Bloat Tool Output Compactor using Out-of-Band SQLite-JSON1 Storage.

Intercepts oversized JSON telemetry payloads (e.g., Cloud Logging / Monitoring dumps),
stores the full JSON array in a local SQLite database, and returns a compact
Schema Skeleton (< 40 lines) while exposing surgical SQL/JSONPath querying via
SQLite's built-in `json_each` and `json_extract` functions.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class CompactedArtifactView:
    """Compact representation returned to the LLM context window."""

    artifact_id: str
    total_records: int
    raw_byte_size: int
    inferred_schema_keys: List[str]
    severity_distribution: Dict[str, int]
    sample_records: List[Dict[str, Any]]

    def to_context_skeleton(self) -> str:
        """Formats the skeleton into a prefix-cache-friendly markdown summary."""
        lines = [
            f"### [COMPACTED_TOOL_ARTIFACT: `{self.artifact_id}`]",
            f"- Total Records: `{self.total_records}` | Raw Payload Size: `{self.raw_byte_size} bytes`",
            f"- Schema Keys: `{', '.join(self.inferred_schema_keys)}`",
            f"- Severity Distribution: `{json.dumps(self.severity_distribution, sort_keys=True)}`",
            "- Sample Records (Top 2):",
            f"```json\n{json.dumps(self.sample_records[:2], indent=2)}\n```",
            f"- Action Hint: Call `query_cached_tool_output('{self.artifact_id}', where_clause)` "
            "to filter specific records without bloating context.",
        ]
        return "\n".join(lines)


class ZeroBloatToolCompactor:
    """Out-of-band SQLite-JSON1 telemetry cache and schema skeleton extractor."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_artifacts (
                artifact_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def ingest_telemetry_payload(
        self,
        artifact_id: str,
        records: List[Dict[str, Any]],
        severity_field: str = "severity",
    ) -> CompactedArtifactView:
        """Stores a raw JSON telemetry list and returns a lightweight Schema Skeleton."""
        raw_json = json.dumps(records)
        self._conn.execute(
            "INSERT OR REPLACE INTO tool_artifacts (artifact_id, payload_json) VALUES (?, ?)",
            (artifact_id, raw_json),
        )
        self._conn.commit()

        key_set = set()
        sev_counter: Counter[str] = Counter()
        for row in records:
            key_set.update(row.keys())
            sev_val = str(row.get(severity_field, "UNKNOWN"))
            sev_counter[sev_val] += 1

        return CompactedArtifactView(
            artifact_id=artifact_id,
            total_records=len(records),
            raw_byte_size=len(raw_json.encode("utf-8")),
            inferred_schema_keys=sorted(key_set),
            severity_distribution=dict(sev_counter),
            sample_records=records[:2],
        )

    def query_cached_tool_output(
        self,
        artifact_id: str,
        json_field: str,
        equals_value: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Executes a surgical `json_each` + `json_extract` query over a cached artifact."""
        json_path = f"$.{json_field}"
        cursor = self._conn.execute(
            """
            SELECT je.value
            FROM tool_artifacts ta,
                 json_each(ta.payload_json) AS je
            WHERE ta.artifact_id = ?
              AND json_extract(je.value, ?) = ?
            LIMIT ?
            """,
            (artifact_id, json_path, equals_value, limit),
        )
        return [json.loads(row[0]) for row in cursor.fetchall()]
