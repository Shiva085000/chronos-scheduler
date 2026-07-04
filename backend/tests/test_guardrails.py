"""Boot-time and edge-validation guardrails."""

import pytest
from pydantic import ValidationError

from app.core.config import DEFAULT_JWT_SECRET, Settings
from app.schemas.job import MAX_PAYLOAD_BYTES, JobCreate


class TestJwtSecretFailFast:
    def test_default_secret_allowed_in_development(self):
        s = Settings(
            environment="development", jwt_secret=DEFAULT_JWT_SECRET, _env_file=None
        )
        assert s.jwt_secret == DEFAULT_JWT_SECRET

    def test_default_secret_refused_outside_development(self):
        with pytest.raises(ValidationError, match="refusing to start"):
            Settings(
                environment="production",
                jwt_secret=DEFAULT_JWT_SECRET,
                _env_file=None,
            )

    def test_real_secret_accepted_in_production(self):
        s = Settings(
            environment="production", jwt_secret="a-real-secret", _env_file=None
        )
        assert s.environment == "production"


class TestPayloadCap:
    def test_payload_under_limit_accepted(self):
        job = JobCreate(task_name="demo.echo", payload={"message": "x" * 1000})
        assert job.payload["message"]

    def test_payload_over_limit_rejected(self):
        oversized = {"blob": "x" * (MAX_PAYLOAD_BYTES + 1)}
        with pytest.raises(ValidationError, match="64KB"):
            JobCreate(task_name="demo.echo", payload=oversized)
