# -*- coding: utf-8 -*-
"""Настройки портала. Всё — из окружения, значения по умолчанию годятся
для запуска на ноутбуке без внешних сервисов."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _b(name, default=False):
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in ('1', 'true', 'yes', 'on')


def database_url():
    """Render отдаёт postgres://, SQLAlchemy 2 ждёт postgresql+psycopg://."""
    url = os.getenv('DATABASE_URL', '').strip()
    if not url:
        return 'postgresql+psycopg://postgres:postgres@localhost:5432/svod'
    if url.startswith('postgres://'):
        url = 'postgresql+psycopg://' + url[len('postgres://'):]
    elif url.startswith('postgresql://'):
        url = 'postgresql+psycopg://' + url[len('postgresql://'):]
    return url


DATABASE_URL = database_url()
REDIS_URL = os.getenv('REDIS_URL', '').strip()
QUEUE_NAME = os.getenv('QUEUE_NAME', 'intake')

# --- хранилище файлов
# s3    — R2/B2/S3: браузер льёт файл прямо в бакет по подписанной ссылке
# local — папка на диске, для запуска на ноутбуке
STORAGE_BACKEND = os.getenv('STORAGE_BACKEND', 's3' if os.getenv('S3_BUCKET') else 'local')
STORAGE_DIR = Path(os.getenv('STORAGE_DIR', BASE_DIR / 'var' / 'files'))
S3_BUCKET = os.getenv('S3_BUCKET', '')
S3_ENDPOINT = os.getenv('S3_ENDPOINT', '')          # https://<account>.r2.cloudflarestorage.com
S3_REGION = os.getenv('S3_REGION', 'auto')
S3_ACCESS_KEY = os.getenv('S3_ACCESS_KEY_ID', '')
S3_SECRET_KEY = os.getenv('S3_SECRET_ACCESS_KEY', '')
S3_PREFIX = os.getenv('S3_PREFIX', 'documents')
UPLOAD_TTL = int(os.getenv('UPLOAD_TTL', '3600'))

MAX_UPLOAD_MB = int(os.getenv('MAX_UPLOAD_MB', '80'))
ORG_NAME = os.getenv('ORG_NAME', 'Внутренняя экспертиза')
APP_NAME = os.getenv('APP_NAME', 'СВОД')
RUN_MIGRATIONS = _b('RUN_MIGRATIONS', True)
# разбор в отдельном потоке того же процесса — только для локального запуска
INLINE_WORKER = _b('INLINE_WORKER', not REDIS_URL)
