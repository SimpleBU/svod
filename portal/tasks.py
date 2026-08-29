# -*- coding: utf-8 -*-
"""Задача воркера: разобрать том.

Скачать из хранилища во временный файл, разобрать фасадом agent.api,
записать листы, спецификацию, паспорт тома и план проверки, обновлять
прогресс. Временный файл живёт только внутри задачи: файловая система
Render эфемерна.

Решения эксперта в плане проверки переносятся между прогонами по ключу
позиции, а не по id строки: при повторном разборе id меняются, позиция
остаётся той же.
"""
import logging
import re
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, func, insert, select

from . import models
from .db import SessionLocal
from .models import (CheckItem, CheckPlan, CheckRule, DeclaredSheet, DocRef,
                     Document, MatchItem, NormRef, RevisionEntry, Run, Sheet,
                     SpecItem, Submission, Symbol)
from .storage import get_storage, page_key, symbol_key

log = logging.getLogger(__name__)

# вес стадий в общем прогрессе: классификация и спецификация — основное время
STAGE_WEIGHTS = [
    ('классификация листов', 0.0, 0.40),
    ('разбор спецификации', 0.40, 0.35),
    ('проверка готовности', 0.75, 0.10),
    ('разбор общих данных', 0.85, 0.07),
    ('условные обозначения', 0.92, 0.08),
]
# сверка: измерение труб по геометрии — самая долгая стадия
MATCH_WEIGHTS = [
    ('классификация листов', 0.0, 0.18),
    ('разбор спецификации', 0.18, 0.12),
    ('счёт по планам', 0.30, 0.20),
    ('счёт по схемам', 0.50, 0.08),
    ('метраж по подписям', 0.58, 0.05),
    ('измерение труб по геометрии', 0.63, 0.32),
    ('сведение с спецификацией', 0.95, 0.05),
]
PROGRESS_EVERY = 1.0  # секунда: чаще писать в БД незачем


def _now():
    return datetime.now(timezone.utc)


def _human(stage, done, total):
    if stage == 'условные обозначения':
        return f'условные обозначения, символ {done} из {total}'
    if total <= 1:
        return stage
    return f'{stage}, лист {min(done + 1, total)} из {total}'


def _percent(stage, done, total, weights=None):
    for name, base, share in (weights or STAGE_WEIGHTS):
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

        # --- паспорт тома: что бюро объявило и что с этим не так
        from agent.api import passport, checkplan
        # к шифрам подачи добавляем шифры, встреченные внутри самого тома:
        # спецификацию и кабельный журнал бюро часто подшивает в тот же файл,
        # и «объявлен .СО — в подаче нет» было бы ложной тревогой
        codes = _submission_codes(session, doc) + [s.code for s in result.sheets
                                                   if s.code]
        psp = passport(local, res=result, filename=doc.filename,
                       submission_codes=codes, progress=progress)
        _save_passport(session, doc, psp, codes)

        # --- план проверки: новая версия с переносом решений эксперта
        _save_checkplan(session, doc, result, checkplan(result, psp))

        doc.pages_total = result.pages_total
        doc.kind_counts = result.kind_counts
        doc.capabilities = result.capabilities.as_dict()
        doc.findings = [f.as_dict() for f in psp.findings]
        doc.status = models.DONE
        doc.parsed_at = _now()
        run.status = models.DONE
        run.percent = 100
        run.stage = 'готово'
        run.finished_at = _now()
        run.stats = result.as_dict()
        session.commit()
        log.info('том %s разобран: %s листов, %s позиций спецификации, '
                 '%s расхождений', doc.id, result.pages_total, len(result.spec),
                 len(psp.findings))
        # картинки листов рисуются отдельной задачей: разбор эксперт видит
        # сразу, листы догружаются в фоне
        from .queue import enqueue_render
        enqueue_render(doc.id)
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


# ------------------------------------------------------- картинки листов

def run_render(document_id):
    """Обзорные картинки и миниатюры всех листов тома.

    Идёт отдельной задачей после приёмки: результаты разбора эксперт
    видит сразу, а листы дорисовываются в фоне. Просмотрщик умеет
    нарисовать недостающий лист сам, так что незаконченная задача
    ничего не ломает — только замедляет первое открытие.
    """
    session = SessionLocal()
    tmpdir = None
    try:
        doc = session.get(Document, document_id)
        if doc is None or not doc.file_key:
            return
        storage = get_storage()
        tmpdir = tempfile.TemporaryDirectory(prefix='svod-')
        local = Path(tmpdir.name) / 'том.pdf'
        storage.download_to(doc.file_key, local)

        from agent.render import page_image, THUMB_WIDTH
        import pymupdf as fitz
        pdf = fitz.open(local)
        try:
            done = 0
            for page in range(1, len(pdf) + 1):
                try:
                    im = page_image(pdf, page)
                    storage.put_bytes(page_key(doc.id, page), im.png, 'image/png')
                    th = page_image(pdf, page, width=THUMB_WIDTH)
                    storage.put_bytes(page_key(doc.id, page, 'thumb'), th.png,
                                      'image/png')
                except Exception:
                    log.exception('лист %s тома %s не отрисован', page, doc.id)
                    continue
                done += 1
                if done % 10 == 0:
                    doc.pages_rendered = done
                    session.commit()
        finally:
            pdf.close()
        doc.pages_rendered = done
        session.commit()
        log.info('том %s: отрисовано листов %s', doc.id, done)
    except Exception:
        session.rollback()
        log.exception('том %s: ошибка отрисовки листов', document_id)
    finally:
        if tmpdir is not None:
            tmpdir.cleanup()
        session.close()


# ------------------------------------------------------- этап 3: сверка

def run_match(document_id):
    """Сверка тома с чертежами. Запускается руками: она дороже приёмки.

    Позиции, отобранные экспертом в плане проверки, передаются в фасад —
    строки сверки, которые к ним относятся, помечаются `in_plan`. Не
    отфильтровываются: расхождение по позиции, которую эксперт не отбирал,
    всё равно стоит показать, просто не первым экраном.
    """
    session = SessionLocal()
    run = None
    tmpdir = None
    try:
        doc = session.get(Document, document_id)
        if doc is None:
            log.warning('том %s не найден', document_id)
            return
        run = Run(org_id=doc.org_id, document_id=doc.id, kind='match',
                  status=models.RUNNING, started_at=_now())
        session.add(run)
        session.commit()

        last = [0.0]

        def progress(stage, done, total):
            now = time.monotonic()
            if now - last[0] < PROGRESS_EVERY:
                return
            last[0] = now
            run.stage = _human(stage, done, total)
            run.done, run.total = done, total
            run.percent = _percent(stage, done, total, MATCH_WEIGHTS)
            session.commit()

        tmpdir = tempfile.TemporaryDirectory(prefix='svod-')
        local = Path(tmpdir.name) / 'том.pdf'
        get_storage().download_to(doc.file_key, local)

        keys = _plan_keys(session, doc)
        from agent.api import reconcile
        result = reconcile(local, keys=keys, progress=progress)

        version = (session.scalar(
            select(func.max(MatchItem.version))
            .where(MatchItem.document_id == doc.id)) or 0) + 1
        session.execute(delete(MatchItem).where(MatchItem.document_id == doc.id))
        if result.rows:
            session.execute(insert(MatchItem), [
                {'document_id': doc.id, 'version': version, 'kind': r.kind,
                 'mark': (r.mark or '')[:300], 'marks': r.marks, 'names': r.names,
                 'unit': (r.unit or '')[:20], 'spec_qty': _f(r.spec_qty),
                 'plan_qty': _f(r.plan_qty), 'plan_raw': _f(r.plan_raw),
                 'schema_qty': _f(r.schema_qty), 'schema_raw': _f(r.schema_raw),
                 'exact_qty': str(r.exact_qty)[:40], 'status': r.status[:80],
                 'level': r.level, 'source': (r.source or '')[:80],
                 'keys': r.keys, 'in_plan': r.in_plan,
                 'spec_pages': r.spec_pages, 'plan_pages': r.plan_pages,
                 'schema_pages': r.schema_pages, 'sections': r.sections,
                 'verdict': '', 'comment': ''}
                for r in result.rows])

        doc.match_stats = dict(result.stats, version=version,
                               plan_keys=len(keys))
        doc.matched_at = _now()
        run.status = models.DONE
        run.percent = 100
        run.stage = 'готово'
        run.finished_at = _now()
        run.stats = result.stats
        session.commit()
        log.info('том %s сверен: %s строк, проблемных %s', doc.id,
                 result.stats['rows'], result.stats['problems'])
    except Exception as exc:  # noqa: BLE001 — сообщение уходит эксперту
        session.rollback()
        text = _explain(exc)
        log.error('том %s: ошибка сверки: %s', document_id, traceback.format_exc())
        try:
            if run is not None:
                run = session.get(Run, run.id) or run
                run.status = models.ERROR
                run.error = text
                run.finished_at = _now()
            doc = session.get(Document, document_id)
            if doc is not None:
                doc.match_stats = {'error': text}
            session.commit()
        except Exception:
            log.exception('не удалось записать ошибку сверки тома %s', document_id)
    finally:
        if tmpdir is not None:
            tmpdir.cleanup()
        session.close()


def _f(v):
    """Пустая строка из отчёта -> NULL: в числовой колонке ей не место."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _plan_keys(session, doc):
    """Ключи позиций, отобранных экспертом в свежем плане проверки."""
    plan = session.scalars(
        select(CheckPlan).where(CheckPlan.document_id == doc.id)
        .order_by(CheckPlan.version.desc())).first()
    if plan is None:
        return set()
    return {i.key for i in session.scalars(
        select(CheckItem).where(CheckItem.plan_id == plan.id)).all() if i.included}


# ------------------------------------------------------- паспорт и план

def _flat(code):
    """«ПР-01/24-3-СПСиА.СО(л. 1-3)» и «…СПСиА.СО» — один документ: хвост
    в скобках указывает листы, а не другой шифр."""
    code = re.sub(r'\([^)]*\)', ' ', code or '')
    return re.sub(r'[\s\-–—_.()/]', '', code).upper()


def _present(code, known):
    """Только точное совпадение: шифр тома является началом шифров всех его
    приложений, и сравнение «по началу» пропустило бы не сданный .АЛ1."""
    return bool(code) and code in known


def _submission_codes(session, doc):
    """Шифры томов этой подачи — по ним проверяется «объявлено, но не сдано»."""
    sub = session.get(Submission, doc.submission_id)
    if sub is None:
        return []
    return [d.cipher or d.filename for d in sub.documents]


def _save_passport(session, doc, psp, codes):
    """Ведомости, нормативы, изменения и условные обозначения тома.

    Всё это производно от файла и переписывается целиком: повторный разбор
    не должен оставлять хвосты прошлого прогона.
    """
    for model in (DeclaredSheet, DocRef, NormRef, RevisionEntry):
        session.execute(delete(model).where(model.document_id == doc.id))

    if psp.sheets:
        session.execute(insert(DeclaredSheet), [
            {'document_id': doc.id, 'no': s.no, 'title': s.title,
             'revisions': s.revisions, 'mark': (s.mark or '')[:20],
             'src_page': s.src_page} for s in psp.sheets if s.no])

    known = {_flat(c) for c in codes if c} - {''}
    rows = [(DocRef, d, d.kind) for d in psp.refs] + [(DocRef, v, 'volume')
                                                      for v in psp.volumes]
    if rows:
        session.execute(insert(DocRef), [
            {'document_id': doc.id, 'kind': kind, 'code': d.code[:200],
             'title': d.title, 'sheets_declared': d.sheets_declared,
             'note': d.note_raw, 'present': _flat(d.code) in known,
             'src_page': d.src_page} for _, d, kind in rows])

    if psp.norms:
        session.execute(insert(NormRef), [
            {'document_id': doc.id, 'code': n.code[:120], 'title': n.title,
             'status': n.status, 'replaced_by': (n.replaced_by or '')[:300],
             'note': n.note, 'contextual': n.contextual, 'sources': n.sources}
            for n in psp.norms])

    if psp.revisions:
        session.execute(insert(RevisionEntry), [
            {'document_id': doc.id, 'number': e.number, 'sheets': e.sheets,
             'content': e.content, 'doc_code': (e.doc_code or '')[:200],
             'basis': e.basis, 'src_page': e.src_page} for e in psp.revisions])

    _save_symbols(session, doc, psp)
    session.commit()


def _save_symbols(session, doc, psp):
    """Картинки условных обозначений уезжают в то же хранилище, что и тома:
    общего диска у веба и воркера на Render не бывает."""
    session.execute(delete(Symbol).where(Symbol.document_id == doc.id))
    if not psp.symbols:
        return
    images = {(im.page, im.name): im for im in psp.symbol_images}
    storage = get_storage()
    # «встречается в спецификации» считаем по маркам, а не по всему тексту:
    # короткий код вроде «В1» иначе находится где угодно
    flat_marks = [_flat(m) for m in session.scalars(
        select(SpecItem.mark).where(SpecItem.document_id == doc.id)).all() if m]
    rows = []
    for i, s in enumerate(psp.symbols, 1):
        im = images.get((s.page, s.name))
        key = ''
        if im is not None and im.png:
            key = symbol_key(doc.id, i)
            try:
                storage.put_bytes(key, im.png, 'image/png')
            except Exception:
                log.exception('не удалось записать картинку УГО %s', key)
                key = ''
        code = _flat(s.code)
        rows.append({'document_id': doc.id, 'name': s.name,
                     'code': (s.code or '')[:60], 'page': s.page,
                     'image_key': key,
                     'width': getattr(im, 'width', 0) if im else 0,
                     'height': getattr(im, 'height', 0) if im else 0,
                     'used': bool(code) and any(code in m for m in flat_marks)})
    session.execute(insert(Symbol), rows)


def _save_checkplan(session, doc, result, rows):
    """Новая версия плана проверки с переносом решений эксперта.

    Решения хранятся по ключу позиции: сначала берём решение из прошлой
    версии плана этого тома, затем — правило уровня объекта (оно приходит
    с прошлой подачи). Ручная работа не должна пропадать от повторного
    разбора.
    """
    prev = session.scalars(
        select(CheckPlan).where(CheckPlan.document_id == doc.id)
        .order_by(CheckPlan.version.desc())).first()
    carried = {}
    if prev is not None:
        for it in session.scalars(select(CheckItem)
                                  .where(CheckItem.plan_id == prev.id)).all():
            if it.decision != models.AUTO or it.comment:
                carried[it.key] = (it.decision, it.comment)

    sub = session.get(Submission, doc.submission_id)
    project_id = sub.project_id if sub else None
    if project_id:
        for rule in session.scalars(select(CheckRule)
                                    .where(CheckRule.project_id == project_id)).all():
            carried.setdefault(rule.key, (rule.decision, rule.comment))

    plan = CheckPlan(org_id=doc.org_id, document_id=doc.id,
                     version=(prev.version + 1 if prev else 1),
                     status=models.DRAFT)
    session.add(plan)
    session.flush()

    spec_ids = [r.id for r in session.scalars(
        select(SpecItem).where(SpecItem.document_id == doc.id)
        .order_by(SpecItem.id)).all()]
    payload = []
    for row in rows:
        decision, comment = carried.get(row.key, (models.AUTO, ''))
        payload.append({
            'plan_id': plan.id,
            'spec_item_id': spec_ids[row.index] if row.index < len(spec_ids) else None,
            'key': row.key, 'source': 'spec', 'pos': row.pos[:40], 'name': row.name,
            'mark': row.mark[:300], 'unit': row.unit[:20], 'qty': row.qty,
            'page': row.page, 'score': row.score, 'cls': row.cls,
            'reasons': [{'code': x.code, 'text': x.text, 'weight': x.weight}
                        for x in row.reasons],
            'verifiable_by': row.verifiable_by,
            'evidence': [{'kind': e.kind, 'text': e.text} for e in row.evidence],
            'decision': decision, 'comment': comment})
    if payload:
        session.execute(insert(CheckItem), payload)
    plan.stats = _plan_stats(payload)
    session.commit()
    return plan


def _plan_stats(payload):
    by_cls = {'A': 0, 'B': 0, 'C': 0}
    taken = skipped = 0
    for it in payload:
        by_cls[it['cls']] = by_cls.get(it['cls'], 0) + 1
        if it['decision'] == models.TAKE:
            taken += 1
        elif it['decision'] == models.SKIP:
            skipped += 1
    included = by_cls['A'] + taken - skipped
    return {'total': len(payload), 'proposed': by_cls['A'], 'by_class': by_cls,
            'taken': taken, 'skipped': skipped, 'included': max(0, included)}


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
