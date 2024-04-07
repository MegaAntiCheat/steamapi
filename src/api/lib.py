import os
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from xml.etree import ElementTree
from typing import Self, cast

import requests
import sqlalchemy as sa
from sqlalchemy import Engine, Row
from sqlalchemy.ext.asyncio import AsyncEngine

DEMOS_PATH = os.path.expanduser(os.path.join("~/media", "demos"))
os.makedirs(DEMOS_PATH, exist_ok=True)
logger = logging.getLogger(__name__)


def _make_db_uri(async_url: bool = False) -> str:
    """Correctly make the database URi."""
    user = os.environ["POSTGRES_USER"]
    password = os.environ["POSTGRES_PASSWORD"]
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "8050")
    prefix = "postgresql"
    if async_url:
        prefix = f"{prefix}+asyncpg"

    return f"{prefix}://{user}:{password}@{host}:{port}/demos"


def _make_demo_path(session_id: str) -> os.path:
    """Make the demo path for the current session."""
    return os.path.join(DEMOS_PATH, f"{session_id}.dem")


def _get_latest_session_id(engine: Engine, api_key: str) -> str | None:
    """Get the latest session_id for a user."""
    with engine.connect() as conn:
        latest_session_id = conn.execute(
            # should use a CTE here...
            sa.text(
                "SELECT session_id FROM demo_sessions WHERE start_time = (SELECT MAX(start_time) FROM demo_sessions WHERE api_key = :api_key);"  # noqa
            ),
            {"api_key": api_key},
        ).scalar_one_or_none()

    return latest_session_id


def generate_uuid4_int() -> int:
    """Seems useless, but makes testing easier."""
    return uuid4().int


async def _check_key_exists(engine: AsyncEngine, api_key: str) -> bool:
    """Helper util to determine key existence."""
    async with engine.connect() as conn:
        result = await conn.execute(sa.text("SELECT * FROM api_keys WHERE api_key = :api_key"), {"api_key": api_key})
        data = result.all()
        if not data:
            return False

        return True


async def _check_is_active(engine: AsyncEngine, api_key: str) -> bool:
    """Helper util to determine if a session is active."""
    sql = "SELECT * FROM demo_sessions WHERE api_key = :api_key and active = true;"
    params = {"api_key": api_key}

    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(sql),
            params,
        )

        data = result.all()
        is_active = bool(data)

        return is_active


def _start_session(engine: Engine, api_key: str, session_id: str, demo_name: str, fake_ip: str, map_str: str) -> None:
    """Start a session and persist to DB."""
    with engine.connect() as conn:
        conn.execute(
            sa.text(
                """INSERT INTO demo_sessions (
                    session_id,
                    api_key,
                    demo_name,
                    active,
                    start_time,
                    end_time,
                    fake_ip,
                    map,
                    steam_api_data,
                    ingested,
                    created_at,
                    updated_at
                ) VALUES (
                    :session_id,
                    :api_key,
                    :demo_name,
                    :active,
                    :start_time,
                    :end_time,
                    :fake_ip,
                    :map,
                    :steam_api_data,
                    :ingested,
                    :created_at,
                    :updated_at
                );
                """
            ),
            {
                "session_id": session_id,
                "api_key": api_key,
                "demo_name": demo_name,
                "active": True,
                "start_time": datetime.now().astimezone(timezone.utc).isoformat(),
                "end_time": None,
                "fake_ip": fake_ip,
                "map": map_str,
                "steam_api_data": None,
                "ingested": False,
                "created_at": datetime.now().astimezone(timezone.utc).isoformat(),
                "updated_at": datetime.now().astimezone(timezone.utc).isoformat(),
            },
        )
        conn.commit()


def _close_session(engine: Engine, api_key: str, current_time: datetime) -> None:
    """Close out a session in the DB."""
    with engine.connect() as conn:
        conn.execute(
            sa.text(
                """UPDATE demo_sessions
                SET
                active = False,
                end_time = :end_time,
                updated_at = :updated_at
                WHERE
                active = True AND
                api_key = :api_key;"""
            ),
            {
                "api_key": api_key,
                "end_time": current_time.isoformat(),
                "updated_at": current_time.isoformat(),
            },
        )
        conn.commit()


def _close_session_with_demo(
    engine: Engine, api_key: str, session_id: str, current_time: datetime, demo_path: str
) -> None:
    """Close out a session in the DB."""
    with engine.connect() as conn:
        oid = conn.connection.lobject(mode="w", new_file=demo_path).oid
        conn.execute(
            sa.text(
                """UPDATE demo_sessions
                SET
                active = False,
                end_time = :end_time,
                demo_oid = :demo_oid,
                updated_at = :updated_at
                WHERE
                api_key = :api_key AND
                session_id = :session_id;"""
            ),
            {
                "api_key": api_key,
                "session_id": session_id,
                "end_time": current_time.isoformat(),
                "updated_at": current_time.isoformat(),
                "demo_oid": oid,
            },
        )
        conn.commit()


def _late_bytes(engine: Engine, api_key: str, late_bytes: bytes, current_time: datetime) -> None:
    """Add late bytes to the DB."""
    with engine.connect() as conn:
        conn.execute(
            sa.text(
                """UPDATE demo_sessions
                SET
                late_bytes = :late_bytes,
                updated_at = :updated_at
                WHERE
                api_key = :api_key
                AND updated_at = (
                    SELECT MAX(updated_at) FROM demo_sessions WHERE api_key = :api_key
                );"""
            ),
            {
                "api_key": api_key,
                "late_bytes": late_bytes,
                "updated_at": current_time.isoformat(),
            },
        )
        conn.commit()


def check_steam_id_has_api_key(engine: Engine, steam_id: str) -> str | None:
    """Check that a given steam id has an API key or not."""
    with engine.connect() as conn:
        result = conn.execute(
            sa.text("SELECT api_key FROM api_keys WHERE steam_id = :steam_id"), {"steam_id": steam_id}
        ).scalar_one_or_none()

        return result


def update_api_key(engine: Engine, steam_id: str, new_api_key) -> str | None:
    """Update an API key."""
    with engine.connect() as conn:
        conn.execute(
            sa.text("UPDATE api_keys SET api_key = :new_api_key WHERE steam_id = :steam_id"),
            {"steam_id": steam_id, "new_api_key": new_api_key},
        )
        conn.commit()


def check_steam_id_is_beta_tester(engine: Engine, steam_id: str) -> bool:
    """Check that a given steam id has an API key or not."""
    with engine.connect() as conn:
        result = conn.execute(
            sa.text("SELECT * FROM beta_tester_steam_ids WHERE steam_id = :steam_id"), {"steam_id": steam_id}
        ).one_or_none()

        return bool(result)


def provision_api_key(engine: Engine, steam_id: str, api_key: str) -> None:
    """Provision an API key."""
    with engine.connect() as conn:
        created_at = datetime.now().astimezone(timezone.utc).isoformat()
        updated_at = created_at
        conn.execute(
            sa.text(
                """INSERT INTO api_keys (
                    steam_id, api_key, created_at, updated_at
                    ) VALUES (
                        :steam_id, :api_key, :created_at, :updated_at);"""
            ),
            {"steam_id": steam_id, "api_key": api_key, "created_at": created_at, "updated_at": updated_at},
        )
        conn.commit()


@dataclass
class DemoWrapper:
    demo_id: str = None
    api_key: str = None
    demo_name: str = None
    active: bool = None
    start_time: datetime = None
    end_time: datetime = None
    fake_ip: str = None
    map: str = None
    steam_api_data: str = None
    ingested: bool = None
    demo_oid: int = None
    late_bytes: bytes = None
    created_at: datetime = None
    updated_at: datetime = None
    map_picture_url: str = None

    def get_dict(self) -> dict:
        _demo_len = self.demo_length()
        return {
            "demo_id": self.demo_id,
            "api_key": self.api_key,
            "demo_name": self.demo_name,
            "demo_length": f"{str(_demo_len.seconds // 3600).zfill(2)}:"
                           f"{str(_demo_len.seconds // 60 % 60).zfill(2)}:"
                           f"{str(_demo_len.seconds % 60).zfill(2)}",
            "active": self.active,
            "start_time": self.start_time.strftime("%m/%d/%Y, %H:%M:%S"),
            "end_time": self.end_time.strftime("%m/%d/%Y, %H:%M:%S"),
            "fake_ip": self.fake_ip,
            "map_name": self.map,
            "steam_api_data": self.steam_api_data,
            "ingested": self.ingested,
            "demo_oid": self.demo_oid,
            "late_bytes": self.late_bytes,
            "created_at": self.created_at.strftime("%m/%d/%Y, %H:%M:%S"),
            "updated_at": self.updated_at.strftime("%m/%d/%Y, %H:%M:%S"),
            "map_picture_url": self.map_picture_url,
        }

    def demo_length(self) -> timedelta:
        return self.end_time - self.start_time

    @classmethod
    def from_db_row(cls, row: Row, *, hide_api_key: bool = True) -> Self:
        """
        Construct a DemoWrapper helper dataclass around the returned tuple from sqlalchemy. Making assumptions
        about the tuple order here...
        """
        logger.info(f"Creating DemoWrapper for result: {row}")
        _row_tuple: tuple = cast(tuple, row)  # sqlalchemy.Row is actually a tuple at runtime. This makes the type
        # checker happy.
        result = DemoWrapper(*_row_tuple)
        if hide_api_key:
            result.api_key = '0'
        return result


def get_api_key_info(engine: Engine, steam_id: str) -> list[datetime] | None:
    """
    Get the metadata for an API key (i.e. when was it created, when was it updated)

    Assumptions: the steam_id has an already provisioned API key.
    """
    with engine.connect() as conn:
        result = conn.execute(
            sa.text("SELECT created_at, updated_at FROM api_keys WHERE steam_id = :steam_id"), {"steam_id": steam_id}
        ).one_or_none()

        if not result:
            return None

        return list(result)


def get_api_key(engine: Engine, steam_id: str) -> str | None:
    """
    Get an API key from the DB for the given steam ID
    """
    with engine.connect() as conn:
        result = conn.execute(
            sa.text("SELECT api_key FROM api_keys WHERE steam_id = :steam_id"), {"steam_id": steam_id}
        ).one_or_none()

        if not result:
            return None

        return result[0]


def get_user_demo_info(engine: Engine, api_key: str) -> list[DemoWrapper] | None:
    """
    Get the number of demos that the given api_key has uploaded
    """
    with engine.connect() as conn:
        result = conn.execute(
            sa.text("SELECT * FROM demo_sessions WHERE api_key = :api_key"), {"api_key": api_key}
        ).all()

        if not result:
            return None

        demos: list[DemoWrapper] = []
        for row in result:
            demos.append(DemoWrapper.from_db_row(row))
        return demos
    
    
def is_limited_account(steam_id: str) -> bool:
    """Check if the account is limited or not."""
    response = requests.get(f"https://steamcommunity.com/profiles/{steam_id}?xml=1")
    tree = ElementTree.fromstring(response.content)
    for element in tree:
        if element.tag == "isLimitedAccount":
            limited = bool(int(element.text))
            return limited
