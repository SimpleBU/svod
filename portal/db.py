# -*- coding: utf-8 -*-
"""Подключение к БД и миграции при старте."""
import logging
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from . import config

log = logging.getLogger(__name__)

engine = create_engine(config.DATABASE_URL, pool_pre_ping=True, pool_size=5,
                       max_overflow=5, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                            future=True)


@contextmanager
def session_scope():
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


MIGRATION_LOCK = 8123401  # произвольный ключ advisory lock


def upgrade_schema():
    """alembic upgrade head под advisory lock: веб и воркер стартуют разом."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(config.BASE_DIR / 'alembic.ini'))
    cfg.set_main_option('script_location', str(config.BASE_DIR / 'portal' / 'migrations'))
    cfg.set_main_option('sqlalchemy.url', config.DATABASE_URL.replace('%', '%%'))
    is_pg = engine.dialect.name == 'postgresql'
    with engine.begin() as conn:
        if is_pg:
            conn.execute(text('SELECT pg_advisory_lock(:k)'), {'k': MIGRATION_LOCK})
    try:
        command.upgrade(cfg, 'head')
        log.info('схема БД обновлена')
    finally:
        if is_pg:
            with engine.begin() as conn:
                conn.execute(text('SELECT pg_advisory_unlock(:k)'), {'k': MIGRATION_LOCK})
