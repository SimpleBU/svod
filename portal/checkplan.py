# -*- coding: utf-8 -*-
"""План проверки: отбор позиций экспертом.

Единственное, что здесь пишет человек, — `decision` и `comment`. Всё
остальное производно и пересчитывается воркером при следующем разборе.
Массовые действия нарочно не трогают строки с уже выставленным решением:
один клик не должен стирать полчаса ручной работы.
"""
from datetime import datetime, timezone

from sqlalchemy import select

from . import models
from .models import CheckItem, CheckPlan, CheckRule, Submission

CLASS_TITLES = {'A': 'проверять обязательно', 'B': 'проверять выборочно',
                'C': 'не проверять'}

FILTERS = {
    'a': ('только A', lambda i: i.cls == 'A'),
    'changed': ('изменённые', lambda i: any(r.get('code') == 'changed'
                                            for r in (i.reasons or []))),
    'length': ('метраж', lambda i: (i.unit or '').strip().rstrip('.') == 'м'),
    'eye': ('только глазами', lambda i: i.verifiable_by == ['только глазами']),
    'taken': ('отобранные', lambda i: i.included),
}


def _now():
    return datetime.now(timezone.utc)


def current_plan(session, document_id):
    return session.scalars(
        select(CheckPlan).where(CheckPlan.document_id == document_id)
        .order_by(CheckPlan.version.desc())).first()


def items(session, plan_id):
    return session.scalars(
        select(CheckItem).where(CheckItem.plan_id == plan_id)
        .order_by(CheckItem.score.desc(), CheckItem.name)).all()


def stats(rows):
    """Счётчик над таблицей: сколько отобрано и чья это заслуга."""
    total = len(rows)
    included = [i for i in rows if i.included]
    taken = sum(1 for i in rows if i.decision == models.TAKE)
    skipped = sum(1 for i in rows if i.decision == models.SKIP)
    return {'total': total, 'included': len(included),
            'percent': round(len(included) * 100 / total) if total else 0,
            'proposed': sum(1 for i in rows if i.cls == 'A'),
            'taken': taken, 'skipped': skipped}


def filtered(rows, q='', flt=''):
    out = rows
    fn = FILTERS.get(flt, (None, None))[1]
    if fn:
        out = [i for i in out if fn(i)]
    q = (q or '').strip().lower()
    if q:
        out = [i for i in out
               if q in (i.mark or '').lower() or q in (i.name or '').lower()]
    return out


def set_decision(session, item, value, project_id=None, submission_id=None):
    """Решение эксперта по одной позиции. Заодно запоминается на объекте:
    следующая подача начнётся не с нуля."""
    if value not in (models.AUTO, models.TAKE, models.SKIP):
        raise ValueError('неизвестное решение')
    item.decision = value
    item.decided_at = _now() if value != models.AUTO else None
    if project_id:
        _remember(session, project_id, item, submission_id)
    session.commit()
    return item


def _remember(session, project_id, item, submission_id=None):
    rule = session.scalar(select(CheckRule)
                          .where(CheckRule.project_id == project_id,
                                 CheckRule.key == item.key))
    if item.decision == models.AUTO and not item.comment:
        if rule is not None:
            session.delete(rule)
        return
    if rule is None:
        rule = CheckRule(project_id=project_id, key=item.key,
                         from_submission_id=submission_id)
        session.add(rule)
    rule.decision = item.decision
    rule.comment = item.comment


def bulk(session, rows, value, overwrite=False, project_id=None, submission_id=None):
    """Массовое действие. -> (изменено, оставлено как есть).

    Строки с решением эксперта не трогаются, пока он явно не попросил
    перезаписать: иначе «взять все A» стирает ручной отбор.
    """
    changed = kept = 0
    for item in rows:
        if item.decision != models.AUTO and not overwrite:
            kept += 1
            continue
        if item.decision == value:
            continue
        item.decision = value
        item.decided_at = _now() if value != models.AUTO else None
        if project_id:
            _remember(session, project_id, item, submission_id)
        changed += 1
    session.commit()
    return changed, kept


def freeze(session, plan, rows):
    plan.status = models.FROZEN
    plan.frozen_at = _now()
    plan.stats = dict(plan.stats or {}, **stats(rows))
    session.commit()
    return plan


def project_of(session, doc):
    sub = session.get(Submission, doc.submission_id)
    return (sub.project_id if sub else None), doc.submission_id
