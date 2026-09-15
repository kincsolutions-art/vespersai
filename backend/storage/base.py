from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Application metadata only. DBOS owns its separate database."""
