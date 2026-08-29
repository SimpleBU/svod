# -*- coding: utf-8 -*-
"""Подключение к БД и миграции при старте."""
import logging
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from . import config

log = logging.getLogger(__name__)

_engine_kwargs = dict(pool_pre_ping=True, future=True)
if config.DATABASE_URL.startswith('sqlite:'):
    _engine_kwargs['connect_args'] = {'check_same_thread': False}
else:
    _engine_kwargs.update(pool_size=5, max_overflow=5)

engine = create_engine(config.DATABASE_URL, **_engine_kwargs)
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
    """Обновить схему БД.

    В боевом PostgreSQL применяем Alembic под advisory lock. Для временного
    демо на SQLite создаём актуальную схему из ORM-моделей: миграции проекта
    содержат PostgreSQL-специфичные default-выражения и для SQLite не нужны.
    """
    if engine.dialect.name == 'sqlite':
        from .models import Base
        Base.metadata.create_all(engine)
        log.info('схема SQLite создана/проверена')
        return

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(config.BASE_DIR / 'alembic.ini'))
    cfg.set_main_option('script_location', str(config.BASE_DIR / 'portal' / 'migrations'))
    cfg.set_main_option('sqlalchemy.url', config.DATABASE_URL.replace('%', '%%'))

    if engine.dialect.name != 'postgresql':
        command.upgrade(cfg, 'head')
        log.info('схема БД обновлена')
        return

    # Advisory lock должен жить на том же соединении всё время выполнения
    # Alembic. Если вернуть соединение в pool раньше, web и worker могут
    # одновременно начать миграции при старте.
    with engine.connect() as lock_conn:
        lock_conn.execute(text('SELECT pg_advisory_lock(:k)'), {'k': MIGRATION_LOCK})
        try:
            command.upgrade(cfg, 'head')
            log.info('схема БД обновлена')
        finally:
            lock_conn.execute(text('SELECT pg_advisory_unlock(:k)'), {'k': MIGRATION_LOCK})
