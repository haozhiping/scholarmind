"""Runtime schema migrations — idempotent, safe to run every process start.

MySQL docker-entrypoint-initdb.d scripts only run on first container init
(empty data dir). This module ensures that schema changes are applied to
existing databases as well, by checking INFORMATION_SCHEMA before each DDL.

Usage:  await run_migrations()   # call once on startup
"""

from common.logging import logger
from common.db.mysql_client import mysql

_MIGRATIONS = [
    (
        "001_add_content_zh_to_doc_blocks",
        # Check: does the column exist?
        "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='doc_blocks' AND COLUMN_NAME='content_zh'",
        # Apply: add the column
        "ALTER TABLE doc_blocks ADD COLUMN content_zh TEXT NULL AFTER content",
    ),
]


async def run_migrations() -> None:
    """Apply any pending schema migrations. Idempotent — checks before each DDL."""
    await mysql.connect()

    for name, check_sql, apply_sql in _MIGRATIONS:
        row = await mysql.fetchone(check_sql)
        if row is not None:
            logger.debug(f"[migration] {name}: already applied, skip")
            continue

        logger.info(f"[migration] {name}: applying...")
        try:
            await mysql.execute(apply_sql)
            logger.info(f"[migration] {name}: applied successfully")
        except Exception as e:
            logger.error(f"[migration] {name}: failed — {e}")
            raise
