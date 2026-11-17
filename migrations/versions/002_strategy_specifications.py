# migrations/versions/002_strategy_specifications.py
"""Add strategy_specifications table

Revision ID: 002
Revises: 001
Create Date: 2024-01-01 00:00:01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create the strategystatus enum type first
    strategystatus = sa.Enum(
        "draft",
        "under_review",
        "approved",
        "rejected",
        "superseded",
        name="strategystatus",
        create_type=True,
    )

    op.create_table(
        "strategy_specifications",

        sa.Column("id", UUID(as_uuid=True), primary_key=True),

        # FK to papers table
        sa.Column(
            "paper_id",
            UUID(as_uuid=True),
            sa.ForeignKey("papers.id", ondelete="RESTRICT"),
            nullable=False,
        ),

        # Versioning
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("variant", sa.String(100), nullable=False, server_default="base"),

        # Status
        sa.Column(
            "status",
            strategystatus,
            nullable=False,
            server_default="draft",
        ),

        # Classification
        sa.Column("completeness_class", sa.String(100), nullable=False),

        # Artifact
        sa.Column("file_path", sa.Text, nullable=True),

        # Provenance
        sa.Column("extracted_by", sa.String(50), nullable=False),
        sa.Column("reviewed_by", sa.String(255), nullable=True),

        # Timestamps
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

    # Indices
    op.create_index(
        "ix_strategy_specifications_paper_id",
        "strategy_specifications",
        ["paper_id"],
    )
    op.create_index(
        "ix_strategy_specifications_status",
        "strategy_specifications",
        ["status"],
    )

    # Uniqueness: one spec per (paper, version, variant)
    op.create_unique_constraint(
        "uq_strategy_paper_version_variant",
        "strategy_specifications",
        ["paper_id", "version", "variant"],
    )


def downgrade() -> None:
    op.drop_table("strategy_specifications")
    sa.Enum(name="strategystatus").drop(op.get_bind(), checkfirst=True)
