from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

_NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative root for every table this service owns. The naming convention is set here rather than left
    to the database because Alembic autogenerate can only emit a `DROP CONSTRAINT` for a constraint whose name
    it can predict, and unnamed constraints are what make a downgrade impossible to write."""

    metadata = MetaData(naming_convention=_NAMING_CONVENTION)
