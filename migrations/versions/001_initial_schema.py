# migrations/versions/001_initial_schema.py

"""Initial schema - papers table

Revision ID: 001
Revises:
Create Date: 2024-01-01 00:00:00
"""
from typing import Sequence, Union
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSON
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "papers",

        # --- Identity ---
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("doi", sa.String(255), nullable=True),
        sa.Column("arxiv_id", sa.String(100), nullable=True),

        # --- Bibliographic Metadata ---
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("abstract", sa.Text, nullable=True),
        sa.Column("published_date", sa.Date, nullable=True),
        sa.Column("journal", sa.String(500), nullable=True),
        sa.Column("authors", JSON, nullable=False, server_default="[]"),
        sa.Column("keywords", JSON, nullable=False, server_default="[]"),

        # --- Source Provenance ---
        sa.Column("sources", JSON, nullable=False, server_default="[]"),

        # --- Evaluation Scores ---
        sa.Column("relevance_score", sa.Float, nullable=True),
        sa.Column("credibility_score", sa.Float, nullable=True),
        sa.Column("implementability_score", sa.Float, nullable=True),
        sa.Column("reproducibility_score", sa.Float, nullable=True),
        sa.Column("priority_score", sa.Float, nullable=True),
        sa.Column("composite_score", sa.Float, nullable=True),
        sa.Column("evaluation", JSON, nullable=True),

        # --- Pipeline State ---
        sa.Column(
            "state",
            sa.Enum(
                "discovered", "normalized", "evaluated",
                "candidate", "approved", "acquired",
                "processed", "rejected",
                name="paperstate",
                create_type=True,
            ),
            nullable=False,
            server_default="discovered",
        ),

        # --- Acquisition ---
        sa.Column("file_path", sa.Text, nullable=True),
        sa.Column("file_hash", sa.String(64), nullable=True),
        sa.Column("file_size_bytes", sa.Integer, nullable=True),

        # --- Timestamps ---
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_index("ix_papers_doi", "papers", ["doi"])
    op.create_index("ix_papers_arxiv_id", "papers", ["arxiv_id"])
    op.create_index("ix_papers_state", "papers", ["state"])
    op.create_unique_constraint("uq_papers_doi", "papers", ["doi"])
    op.create_unique_constraint("uq_papers_arxiv_id", "papers", ["arxiv_id"])


def downgrade() -> None:
    op.drop_table("papers")
    sa.Enum(name="paperstate").drop(op.get_bind(), checkfirst=True)
    op.drop_table("papers")
    sa.Enum(name="paperstate").drop(op.get_bind(), checkfirst=True)