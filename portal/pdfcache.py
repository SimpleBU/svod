# -*- coding: utf-8 -*-
"""Локальный кэш томов рядом с процессом, который рисует листы.

Растр листа стоит доли секунды, а скачивание тома из хранилища — секунды
и сотни мегабайт трафика. Поэтому файл, с которым сейчас работает эксперт,
лежит на диске инстанса, и повторные кропы под зум его не перекачивают.

Диск на Render эфемерный: кэш можно потерять в любой момент, и ничего
не случится — файл скачается заново. Ничего, кроме копий из хранилища,
здесь не хранится.
"""
import logging
import os
import tempfile
import threading
from pathlib import Path

from . import config
from .storage import get_storage

log = logging.getLogger(__name__)

# потолок кэша: держим несколько томов, дальше вытесняем по времени доступа
MAX_BYTES = int(os.getenv('PDF_CACHE_MB', '2048')) * 1024 * 1024

_lock = threading.Lock()
_root = None


def root() -> Path:
    global _root
    if _root is None:
        _root = Path(os.getenv('PDF_CACHE_DIR')
                     or (Path(tempfile.gettempdir()) / 'svod-pdf'))
        _root.mkdir(parents=True, exist_ok=True)
    return _root


def _sweep():
    """Вытеснение по времени последнего обращения, пока не влезем в потолок."""
    files = [(p.stat().st_atime, p.stat().st_size, p)
             for p in root().glob('*.pdf') if p.is_file()]
    total = sum(s for _, s, _ in files)
    for _, size, path in sorted(files):
        if total <= MAX_BYTES:
            break
        try:
            path.unlink()
            total -= size
        except OSError:
            pass


def local_path(document_id, file_key) -> Path:
    """Путь к тому на локальном диске; скачивает, если его ещё нет."""
    path = root() / f'{document_id}.pdf'
    if path.exists() and path.stat().st_size:
        os.utime(path, None)                 # обновляем время доступа для LRU
        return path
    with _lock:
        if path.exists() and path.stat().st_size:
            return path
        tmp = path.with_suffix('.part')
        get_storage().download_to(file_key, tmp)
        tmp.replace(path)
        log.info('том %s скачан в кэш (%.0f МБ)', document_id,
                 path.stat().st_size / 1048576)
        _sweep()
    return path


def forget(document_id):
    p = root() / f'{document_id}.pdf'
    try:
        p.unlink()
    except OSError:
        pass
