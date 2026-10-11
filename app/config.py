"""Application settings.

Every value has a default that works out of the box and can be overridden with
an environment variable, e.g. `DATABASE_URL=postgresql+psycopg://... uvicorn ...`.
"""
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    # Any SQLAlchemy URL for a relational database. SQLite needs no server.
    database_url: str = "sqlite:///./certificates.db"
    # Generated PDFs are written under this folder.
    storage_dir: Path = Path("storage")
    # Upper bound on recipients in one request, to keep request size bounded.
    max_recipients_per_job: int = 10_000

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            storage_dir=Path(os.getenv("STORAGE_DIR", str(cls.storage_dir))),
            max_recipients_per_job=int(
                os.getenv("MAX_RECIPIENTS_PER_JOB", str(cls.max_recipients_per_job))
            ),
        )