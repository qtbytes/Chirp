from sqlalchemy import Engine, MetaData, inspect, text
from sqlalchemy.schema import CreateColumn


def sync_sqlite_dev_schema(engine: Engine, metadata: MetaData) -> None:
    """
    Keep local SQLite usable during rapid model changes.

    SQLAlchemy create_all() creates missing tables, but it intentionally does
    not alter existing tables. For this learning project, we add missing
    nullable columns automatically so a local dev database can survive small
    model changes without being deleted.
    """
    metadata.create_all(bind=engine)

    if not engine.url.drivername.startswith("sqlite"):
        return

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    preparer = engine.dialect.identifier_preparer

    with engine.begin() as connection:
        for table in metadata.sorted_tables:
            if table.name not in table_names:
                continue

            existing_column_names = {
                column["name"] for column in inspector.get_columns(table.name)
            }
            for column in table.columns:
                if column.name in existing_column_names:
                    continue

                if not column.nullable and column.default is None and column.server_default is None:
                    continue

                column_sql = str(CreateColumn(column).compile(dialect=engine.dialect))
                connection.execute(
                    text(
                        "ALTER TABLE "
                        f"{preparer.quote(table.name)} "
                        f"ADD COLUMN {column_sql}"
                    )
                )
