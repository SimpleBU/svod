# -*- coding: utf-8 -*-
"""Задача воркера: разобрать том.

Скачать из хранилища во временный файл, разобрать фасадом agent.api,
записать листы и спецификацию, обновлять прогресс. Временный файл живёт
только внутри задачи: файловая система Render эфемерна.
"""
import logging
import re
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, insert

from . import models
from .db import SessionLocal
from .models import Document, Run, Sheet, SpecItem
from .storage import get_storage

log = logging.getLogger(__name__)

# вес стадий в общем прогрессе: классификация и спецификация — основное время
STAGE_WEIGHTS = [
    ('классификация листов', 0.0, 0.45),
    ('разбор спецификации', 0.45, 0.40),
    ('проверка готовности', 0.85, 0.15),
]
PROGRESS_EVERY = 1.0  # секунда: чаще писать в БД незачем


def _now():
    return datetime.now(timezone.utc)


def _human(stage, done, total):
    if stage == 'классификация листов':
        return f'классификация листов, лист {done + 1} из {total}'
    if stage == 'разбор спецификации':
        return f'разбор спецификации, лист {done + 1} из {total}'
    return f'проверка готовности, лист {done + 1} из {total}'


def _percent(stage, done, total):
    for name, base, share in STAGE_WEIGHTS:
        if name == stage:
            frac = (done / total) if total else 0
            return int(round((base + share * min(frac, 1.0)) * 100))
    return 0


def run_intake(document_id):
    """Точка входа задачи. Всё, что может сломаться, ломается внятно."""
    session = SessionLocal()
    run = None
    tmpdir = None
    try:
        doc = session.get(Document, document_id)
        if doc is None:
            log.warning('том %s не найден', document_id)
            return
        run = Run(org_id=doc.org_id, document_id=doc.id, kind='intake',
                  status=models.RUNNING, started_at=_now())
        session.add(run)
        doc.status = models.RUNNING
        doc.error = ''
        session.commit()

        last = [0.0]

        def progress(stage, done, total):
            now = time.monotonic()
            if now - last[0] < PROGRESS_EVERY:
                return
            last[0] = now
            run.stage = _human(stage, done, total)
            run.done, run.total = done, total
            run.percent = _percent(stage, done, total)
            session.commit()

        tmpdir = tempfile.TemporaryDirectory(prefix='svod-')
        local = Path(tmpdir.name) / 'том.pdf'
        get_storage().download_to(doc.file_key, local)
        doc.size_bytes = doc.size_bytes or local.stat().st_size

        from agent.api import intake
        result = intake(local, progress=progress)

        session.execute(delete(Sheet).where(Sheet.document_id == doc.id))
        session.execute(delete(SpecItem).where(SpecItem.document_id == doc.id))
        if result.sheets:
            session.execute(insert(Sheet), [
                {'document_id': doc.id, 'page': s.page, 'kind': s.kind,
                 'kind_override': '', 'code': (s.code or '')[:120],
                 'title': (s.title or '')[:300], 'mult': s.mult}
                for s in result.sheets])
        if result.spec:
            session.execute(insert(SpecItem), [
                {'document_id': doc.id, 'page': r.page, 'pos': r.pos[:40],
                 'name': r.name, 'mark': r.mark[:300], 'canon_mark': r.canon[:300],
                 'unit': r.unit[:20], 'qty': r.qty, 'qty_raw': r.qty_raw[:80],
                 'section': r.section[:80], 'category': r.category[:300],
                 'note': r.note, 'excluded': r.excluded, 'composite': r.composite,
                 'component_of': (r.component_of or '')[:120],
                 'expanded_range': r.expanded_range}
                for r in result.spec])

        doc.pages_total = result.pages_total
        doc.kind_counts = result.kind_counts
        doc.capabilities = result.capabilities.as_dict()
        doc.status = models.DONE
        doc.parsed_at = _now()
        run.status = models.DONE
        run.percent = 100
        run.stage = 'готово'
        run.finished_at = _now()
        run.stats = result.as_dict()
        session.commit()
        log.info('том %s разобран: %s листов, %s позиций спецификации',
                 doc.id, result.pages_total, len(result.spec))
    except Exception as exc:  # noqa: BLE001 — сообщение уходит эксперту
        session.rollback()
        text = _explain(exc)
        log.error('том %s: ошибка разбора: %s', document_id, traceback.format_exc())
        try:
            doc = session.get(Document, document_id)
            if doc:
                doc.status = models.ERROR
                doc.error = text
            if run is not None:
                run = session.get(Run, run.id) or run
                run.status = models.ERROR
                run.error = text
                run.finished_at = _now()
            session.commit()
        except Exception:
            log.exception('не удалось записать ошибку тома %s', document_id)
    finally:
        if tmpdir is not None:
            tmpdir.cleanup()
        session.close()


def _explain(exc):
    """Человеческая формулировка вместо трассировки: эксперт должен понять,
    что делать дальше, а не читать путь к временному файлу."""
    msg = re.sub(r'/\S*svod-\S*', 'файл', str(exc))
    low = msg.lower()
    if isinstance(exc, MemoryError) or 'memory' in low:
        return 'Не хватило памяти на разбор тома — нужен воркер большего размера.'
    if 'password' in low or 'encrypted' in low or 'authenticate' in low:
        return ('PDF защищён паролем на извлечение содержимого. Запросите '
                'у бюро копию без ограничений или снимите защиту.')
    if 'no such file' in low or 'nosuchkey' in low or '404' in low:
        return 'Файл не найден в хранилище — загрузите том заново.'
    if ('as type pdf' in low or 'cannot open' in low or 'failed to open' in low
            or 'damaged' in low or 'format error' in low or 'no objects found' in low):
        return ('Файл не открывается как PDF: он повреждён или загрузка '
                'оборвалась. Загрузите том заново.')
    return f'Не удалось разобрать файл: {msg[:300]}'
