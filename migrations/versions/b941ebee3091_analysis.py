"""analysis

Revision ID: b941ebee3091
Revises: 53d7f00c595e
Create Date: 2024-10-08 13:56:46.796256

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b941ebee3091'
down_revision: Union[str, None] = '53d7f00c595e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add analysis and review tables."""
    op.execute(
        """
        CREATE TABLE analysis (
            session_id varchar REFERENCES demo_sessions,
            target_steam_id varchar,
            created_at timestamptz,
            detection_count int,
            PRIMARY KEY (session_id, target_steam_id)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE reviews (
            session_id varchar REFERENCES demo_sessions,
            target_steam_id varchar,
            reviewer_steam_id varchar,
            created_at timestamptz,
            verdict varchar,
            PRIMARY KEY (session_id, target_steam_id, reviewer_steam_id)
        );
        """
    )


def downgrade() -> None:
    """Remove analysis and review tables."""
    op.execute(
        """
        DROP TABLE analysis;
        DROP TABLE reviews;
        """
    )
