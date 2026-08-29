# -*- coding: utf-8 -*-
"""Вкладка «Сверка с чертежами» (этап 3).

Веб только читает: сверку считает воркер, здесь строки из БД
раскладываются в порядок, в котором их читает эксперт.

Порядок один и тот же везде: сначала то, что эксперт сам отобрал в план
проверки, потом уровень расхождения, потом марка. Строка «нет на
чертежах» по позиции, которую никто не отбирал, — самая частая и самая
бесполезная, и она обязана лежать в конце.
"""
from sqlalchemy import select

from . import models, remarks as remark_service
from .models import MatchItem, Run

LEVELS = {'red': 'r', 'amber': 'y', 'ok': 'g'}
LEVEL_ORDER = {'red': 0, 'amber': 1, 'ok': 2}

FILTERS = {
    'plan': ('по плану проверки', lambda i: i.in_plan),
    'problems': ('расхождения', lambda i: i.level == 'red'),
    'doubts': ('под вопросом', lambda i: i.level == 'amber'),
    'missing': ('нет на чертежах',
                lambda i: (i.status or '').startswith('нет на чертежах')),
    'length': ('метраж', lambda i: i.kind == 'length'),
    'undecided': ('без решения', lambda i: not getattr(i, 'remark', None)),
}

MAX_ROWS = 800


def current_run(session, document_id):
    """Последний прогон сверки по тому — из него берётся прогресс."""
    return session.scalars(
        select(Run).where(Run.document_id == document_id, Run.kind == 'match')
        .order_by(Run.id.desc())).first()


def items(session, document_id):
    """Строки сверки с привязанным решением эксперта.

    Решение живёт в отдельной таблице и переживает пересверку, поэтому
    подтягивается по ключу, а не по id строки.
    """
    rows = session.scalars(
        select(MatchItem).where(MatchItem.document_id == document_id)).all()
    known = remark_service.by_key(session, document_id)
    for i in rows:
        i.remark = known.get(remark_service.match_key(i))
    return sorted(rows, key=lambda i: (not i.in_plan,
                                       LEVEL_ORDER.get(i.level, 3),
                                       i.mark or ''))


def stats(rows):
    return {
        'total': len(rows),
        'problems': sum(1 for i in rows if i.level == 'red'),
        'doubts': sum(1 for i in rows if i.level == 'amber'),
        'matched': sum(1 for i in rows if i.level == 'ok'),
        'in_plan': sum(1 for i in rows if i.in_plan),
        'in_plan_problems': sum(1 for i in rows if i.in_plan and i.level == 'red'),
    }


def filtered(rows, q='', flt=''):
    fn = FILTERS.get(flt, (None, None))[1]
    out = [i for i in rows if fn(i)] if fn else rows
    q = (q or '').strip().lower()
    if q:
        out = [i for i in out
               if q in (i.mark or '').lower() or q in (i.names or '').lower()
               or any(q in (m or '').lower() for m in (i.marks or []))]
    return out


def default_filter(rows):
    """Что показать до того, как эксперт что-то выбрал.

    Если план проверки отобран — его позиции; иначе расхождения; если и их
    нет — всё, иначе экран выглядит пустым при удачной сверке.
    """
    if any(i.in_plan for i in rows):
        return 'plan'
    if any(i.level == 'red' for i in rows):
        return 'problems'
    return ''


def context(session, doc, q='', flt=None):
    rows = items(session, doc.id)
    run = current_run(session, doc.id)
    busy = run is not None and run.status in (models.QUEUED, models.RUNNING)
    if flt is None:
        flt = default_filter(rows)
    shown = filtered(rows, q, flt)
    for i in shown:
        i.level_class = LEVELS.get(i.level, '')
    return {
        'doc': doc, 'run': run, 'busy': busy,
        'error': (doc.match_stats or {}).get('error', ''),
        'has_run': bool(rows) or doc.matched_at is not None,
        'rows': shown[:MAX_ROWS], 'truncated': len(shown) > MAX_ROWS,
        'stats': stats(rows), 'filters': FILTERS, 'q': q, 'flt': flt,
        'plan_keys': (doc.match_stats or {}).get('plan_keys', 0),
        'uncheckable': (doc.match_stats or {}).get('uncheckable', 0),
    }
