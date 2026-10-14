"""Read-only application service used by the Arda MCP gateway."""
from __future__ import annotations

from typing import Any

from arda.episodes import EpisodeStore
from arda.experience import SQLiteEvaluationStore
from arda.providers import SQLiteProviderOutputStore


class CoreReadServiceError(ValueError):
    """A requested Core projection is unavailable or fails integrity checks."""


class CoreReadService:
    """Application-level read facade; it never mutates Core state."""

    def __init__(self, db: Any):
        self.db = db
        self.episodes = EpisodeStore(db)
        self.artifacts = SQLiteProviderOutputStore(db)
        self.evaluations = SQLiteEvaluationStore(db)

    def episode_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        episode_id = self._required(arguments, "episode_id")
        episode = self.episodes.get(episode_id)
        events = self.episodes.events(episode_id)
        return {
            "episode_id": episode.episode_id,
            "task_id": episode.task_id,
            "task_contract_revision_id": episode.task_contract_revision_id,
            "task_contract_hash": episode.task_contract_hash,
            "status": episode.status.value,
            "created_at": episode.created_at,
            "updated_at": episode.updated_at,
            "events": [
                {"event_type": event.event_type, "status": event.status.value, "created_at": event.created_at}
                for event in events
            ],
        }

    def verification_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        episode_id = self._required(arguments, "episode_id")
        episode = self.episodes.get(episode_id)
        events = self.episodes.events(episode_id)
        verification_events = [
            {"event_type": event.event_type, "status": event.status.value, "payload": event.payload, "created_at": event.created_at}
            for event in events
            if "verification" in event.event_type or "completion" in event.event_type
        ]
        records = [record for record in self.evaluations.list() if record.episode_id == episode_id]
        return {
            "episode_id": episode_id,
            "episode_status": episode.status.value,
            "verification_events": verification_events,
            "evaluation_count": len(records),
            "evaluation_ids": [record.evaluation_id for record in records],
        }

    def artifact_read(self, arguments: dict[str, Any]) -> dict[str, Any]:
        artifact_id = self._required(arguments, "artifact_id")
        artifact = self.artifacts.get(artifact_id)
        return {
            "artifact_id": artifact.artifact_id,
            "episode_id": artifact.episode_id,
            "request_id": artifact.request_id,
            "provider_id": artifact.provider_id,
            "model_snapshot": artifact.model_snapshot,
            "request_hash": artifact.request_hash,
            "output_hash": artifact.output_hash,
            "content_type": artifact.content_type,
            "size_bytes": len(artifact.content),
            "created_at": artifact.created_at,
            "content_exposed": False,
        }

    def evaluation_read(self, arguments: dict[str, Any]) -> dict[str, Any]:
        episode_id = self._required(arguments, "episode_id")
        records = [record for record in self.evaluations.list() if record.episode_id == episode_id]
        return {
            "episode_id": episode_id,
            "records": [
                {
                    "evaluation_id": record.evaluation_id,
                    "provider_id": record.provider_id,
                    "capability_id": record.capability_id,
                    "model_snapshot": record.model_snapshot,
                    "outcome_status": record.outcome_status,
                    "completion_status": record.completion_status,
                    "latency_ms": record.latency_ms,
                    "canonical_hash": record.canonical_hash,
                }
                for record in records
            ],
        }

    @staticmethod
    def _required(arguments: dict[str, Any], name: str) -> str:
        value = arguments.get(name)
        if not isinstance(value, str) or not value.strip():
            raise CoreReadServiceError(f"{name} must be a non-empty string")
        return value

