# -*- coding: utf-8 -*-
"""Хранилище файлов.

На Render постоянный диск монтируется только к одному сервису, а веб и
воркер — разные машины. Поэтому файлы всегда во внешнем объектном
хранилище (R2/B2/S3), и браузер льёт том прямо туда по подписанной
ссылке — мимо веб-сервиса. Локальный бэкенд нужен только чтобы поднять
портал на ноутбуке без облака; интерфейс загрузки у обоих один.
"""
import os
import shutil
from pathlib import Path
from urllib.parse import quote

from . import config


class Storage:
    def upload_target(self, key, content_type='application/pdf'):
        """-> {'method','url','headers'} для загрузки файла браузером."""
        raise NotImplementedError

    def download_to(self, key, path):
        raise NotImplementedError

    def put_stream(self, key, fileobj):
        raise NotImplementedError

    def put_bytes(self, key, data, content_type='application/octet-stream'):
        import io as _io
        return self.put_stream(key, _io.BytesIO(data))

    def read_bytes(self, key):
        """Небольшой файл целиком: картинки УГО отдаёт веб, а не браузер."""
        raise NotImplementedError

    def exists(self, key):
        raise NotImplementedError


class LocalStorage(Storage):
    def __init__(self, root=None):
        self.root = Path(root or config.STORAGE_DIR)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key):
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise ValueError('ключ вне хранилища')
        return p

    def upload_target(self, key, content_type='application/pdf'):
        return {'method': 'PUT', 'url': '/api/uploads/local/' + quote(key),
                'headers': {'Content-Type': content_type}}

    def put_stream(self, key, fileobj):
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, 'wb') as f:
            shutil.copyfileobj(fileobj, f, 1024 * 1024)
        return p.stat().st_size

    def download_to(self, key, path):
        shutil.copyfile(self._path(key), path)
        return path

    def read_bytes(self, key):
        with open(self._path(key), 'rb') as f:
            return f.read()

    def exists(self, key):
        return self._path(key).exists()


class S3Storage(Storage):
    def __init__(self):
        import boto3
        from botocore.config import Config as BotoConfig
        self.bucket = config.S3_BUCKET
        self.client = boto3.client(
            's3',
            endpoint_url=config.S3_ENDPOINT or None,
            region_name=config.S3_REGION,
            aws_access_key_id=config.S3_ACCESS_KEY,
            aws_secret_access_key=config.S3_SECRET_KEY,
            config=BotoConfig(signature_version='s3v4',
                              retries={'max_attempts': 3, 'mode': 'standard'}))

    def upload_target(self, key, content_type='application/pdf'):
        url = self.client.generate_presigned_url(
            'put_object',
            Params={'Bucket': self.bucket, 'Key': key, 'ContentType': content_type},
            ExpiresIn=config.UPLOAD_TTL)
        return {'method': 'PUT', 'url': url, 'headers': {'Content-Type': content_type}}

    def put_stream(self, key, fileobj):
        self.client.upload_fileobj(fileobj, self.bucket, key)
        return self.client.head_object(Bucket=self.bucket, Key=key)['ContentLength']

    def put_bytes(self, key, data, content_type='application/octet-stream'):
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data,
                               ContentType=content_type)
        return len(data)

    def download_to(self, key, path):
        self.client.download_file(self.bucket, key, str(path))
        return path

    def read_bytes(self, key):
        return self.client.get_object(Bucket=self.bucket, Key=key)['Body'].read()

    def exists(self, key):
        from botocore.exceptions import ClientError
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False


_storage = None


def get_storage():
    global _storage
    if _storage is None:
        _storage = S3Storage() if config.STORAGE_BACKEND == 's3' else LocalStorage()
    return _storage


def symbol_key(document_id, index):
    """Ключ картинки условного обозначения. Лежит там же, где тома:
    общий диск у веба и воркера на Render невозможен."""
    prefix = config.S3_PREFIX.strip('/') if config.STORAGE_BACKEND == 's3' else ''
    return '/'.join(x for x in (prefix, str(document_id), 'symbols',
                                f'{index:03d}.png') if x)


def page_key(document_id, page, kind='overview'):
    """Картинка листа: обзор, миниатюра или кроп под зум.

    Кропы — кэш: их можно потерять и пережить, обзор и миниатюра живут
    вместе с томом.
    """
    prefix = config.S3_PREFIX.strip('/') if config.STORAGE_BACKEND == 's3' else ''
    return '/'.join(x for x in (prefix, str(document_id), 'pages',
                                str(int(page)), f'{kind}.png') if x)


def crop_key(document_id, page, box, width):
    """Ключ кропа: округлённая рамка и ширина. Округление до 0,5 % даёт
    попадание в кэш при повторном заходе в ту же область."""
    b = '_'.join(f'{round(float(v) * 200):d}' for v in box)
    return page_key(document_id, page, f'crop-{b}-{int(width)}')


def object_key(document_id, filename):
    safe = ''.join(c for c in os.path.basename(filename)
                   if c.isalnum() or c in ' .-_()[]').strip() or 'том.pdf'
    prefix = config.S3_PREFIX.strip('/') if config.STORAGE_BACKEND == 's3' else ''
    return '/'.join(x for x in (prefix, str(document_id), safe) if x)
