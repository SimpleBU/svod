# -*- coding: utf-8 -*-
"""Ложные срабатывания: обратная связь алгоритмам.

Эксперт снимает находку как ложную тревогу и пишет, почему. Это не
замечание бюро и в письмо не попадает — это единственный канал, по
которому алгоритмы сверки и разбора паспорта узнают, где они неправы.

Два решения, на которых всё держится:

* причина берётся из справочника, а не только текстом. Сто свободных
  комментариев не складываются в статистику, а с причиной сразу видно,
  какой алгоритм чинить первым: путаницу синонимов марок, кратность
  типовых этажей или разбор ведомости;
* машинный вывод сохраняется снимком в момент решения. Пересверка
  переписывает `match_item` целиком, и назавтра доказательства «на чём
  именно машина ошиблась» уже не существует.
"""
from datetime import datetime, timezone

from sqlalchemy import select

from .models import AlgoFeedback, Document, Project, Submission

# Справочник причин. Ключ — код для анализа, значение — что видит эксперт
# и к каким проверкам причина применима.
REASONS = {
    'other_name': ('на чертеже есть, но подписано иначе', ('match',)),
    'not_drawn': ('позиции и не должно быть на чертежах', ('match',)),
    'miscounted': ('машина посчитала неверно', ('match',)),
    'other_volume': ('оборудование в другом томе или разделе', ('match',)),
    'excluded': ('позиция исключена изменением', ('match',)),
    'unreadable': ('лист не прочитался — на самом деле марка там есть',
                   ('match', 'passport')),
    'spec_error': ('ошибка в спецификации, а не в чертежах', ('match', 'passport')),
    'parsed_wrong': ('машина разобрала документ неверно', ('passport',)),
    'not_required': ('проверка здесь неприменима', ('passport',)),
    'other': ('иное — описано в комментарии', ('match', 'passport')),
}


def reasons_for(source):
    return [(k, v[0]) for k, v in REASONS.items() if source in v[1]]


def reason_label(code):
    return REASONS.get(code, ('иное',))[0]


def _now():
    return datetime.now(timezone.utc)


def _machine(finding):
    """Что заявила машина — снимком, потому что прогон это перепишет."""
    i = finding.item
    if finding.source == 'match' and i is not None:
        return {
            'kind': i.kind, 'mark': i.mark, 'marks': list(i.marks or []),
            'names': i.names, 'unit': i.unit,
            'spec_qty': i.spec_qty, 'plan_qty': i.plan_qty, 'plan_raw': i.plan_raw,
            'schema_qty': i.schema_qty, 'schema_raw': i.schema_raw,
            'exact_qty': i.exact_qty, 'status': i.status, 'level': i.level,
            'measured_by': i.source, 'in_plan': bool(i.in_plan),
            'spec_pages': list(i.spec_pages or []),
            'plan_pages': list(i.plan_pages or []),
            'schema_pages': list(i.schema_pages or []),
            'sections': list(i.sections or []),
            'keys': list(i.keys or []),
            'plan_reasons': [r.get('code') for r in (finding.reasons or [])],
        }
    raw = finding.raw or {}
    return {'code': raw.get('code', ''), 'level': raw.get('level', ''),
            'text': raw.get('text', ''), 'sheets': list(raw.get('sheets') or [])}


def _context(session, doc):
    sub = session.get(Submission, doc.submission_id)
    project = session.get(Project, sub.project_id) if sub else None
    return {
        'project': project.name if project else '',
        'project_code': project.code if project else '',
        'bureau': project.bureau if project else '',
        'submission': sub.label if sub else '',
        'document': doc.filename,
        'cipher': doc.cipher or '',
        'section': doc.section or '',
        'section_label': doc.section_label or '',
        'revision': doc.revision or '',
        'pages_total': doc.pages_total,
        'kind_counts': dict(doc.kind_counts or {}),
    }, (sub.project_id if sub else None), doc.submission_id


def code_of(finding):
    """Что именно ошиблось: код проверки паспорта или статус сверки."""
    if finding.source == 'passport':
        return (finding.raw or {}).get('code', '')
    status = (finding.item.status if finding.item is not None else '') or ''
    return status.split(',')[0].strip()[:80]


def record(session, doc, finding, reason, comment, user_id=None):
    """Записать ложное срабатывание. Повторное снятие переписывает своё же."""
    row = session.scalar(select(AlgoFeedback).where(
        AlgoFeedback.document_id == doc.id, AlgoFeedback.key == finding.key))
    context, project_id, submission_id = _context(session, doc)
    if row is None:
        row = AlgoFeedback(org_id=doc.org_id, document_id=doc.id, key=finding.key)
        session.add(row)
    row.project_id = project_id
    row.submission_id = submission_id
    row.source = finding.source
    row.code = code_of(finding)
    row.subject = (finding.title or '')[:300]
    row.reason = reason if reason in REASONS else 'other'
    row.comment = (comment or '').strip()
    row.machine = _machine(finding)
    row.context = context
    row.author_id = user_id
    row.withdrawn = False
    session.commit()
    return row


def withdraw(session, document_id, key):
    """Эксперт вернул находку в работу — из выгрузки запись уходит."""
    row = session.scalar(select(AlgoFeedback).where(
        AlgoFeedback.document_id == document_id, AlgoFeedback.key == key))
    if row is not None and not row.withdrawn:
        row.withdrawn = True
        session.commit()
    return row


def by_key(session, document_id):
    return {r.key: r for r in session.scalars(select(AlgoFeedback).where(
        AlgoFeedback.document_id == document_id,
        AlgoFeedback.withdrawn == False)).all()}      # noqa: E712


def rows(session, org_id=None, project_id=None, source='', reason=''):
    q = select(AlgoFeedback).where(AlgoFeedback.withdrawn == False)   # noqa: E712
    if org_id:
        q = q.where(AlgoFeedback.org_id == org_id)
    if project_id:
        q = q.where(AlgoFeedback.project_id == project_id)
    if source:
        q = q.where(AlgoFeedback.source == source)
    if reason:
        q = q.where(AlgoFeedback.reason == reason)
    return session.scalars(q.order_by(AlgoFeedback.created_at.desc())).all()


def stats(items):
    by_reason, by_code = {}, {}
    for r in items:
        by_reason[r.reason] = by_reason.get(r.reason, 0) + 1
        key = r.code or '—'
        by_code[key] = by_code.get(key, 0) + 1
    return {
        'total': len(items),
        'match': sum(1 for r in items if r.source == 'match'),
        'passport': sum(1 for r in items if r.source == 'passport'),
        'by_reason': sorted(by_reason.items(), key=lambda kv: -kv[1]),
        'by_code': sorted(by_code.items(), key=lambda kv: -kv[1]),
    }


def payload(items, authors=None):
    """Пачка для анализа. Плоский json: его читает не портал, а разбор."""
    authors = authors or {}
    return {
        'exported_at': _now().isoformat(),
        'schema': 'svod.algo_feedback/1',
        'reasons': {k: v[0] for k, v in REASONS.items()},
        'count': len(items),
        'items': [{
            'id': r.id,
            'created_at': r.created_at.isoformat() if r.created_at else None,
            'source': r.source,
            'code': r.code,
            'subject': r.subject,
            'reason': r.reason,
            'reason_label': reason_label(r.reason),
            'comment': r.comment,
            'author': authors.get(r.author_id, ''),
            'machine': r.machine,
            'context': r.context,
        } for r in items],
    }


# колонки выгрузки в Excel — то же, что в json, но читаемое глазами
SHEET_COLUMNS = (
    ('Дата', 22), ('Объект', 26), ('Том', 22), ('Раздел', 16),
    ('Проверка', 18), ('Предмет', 30), ('Что заявила машина', 30),
    ('Спец.', 10), ('Планы', 10), ('Схемы', 10), ('Листы', 18),
    ('Причина', 34), ('Комментарий эксперта', 60), ('Эксперт', 20),
)


def sheet_rows(items, authors=None):
    authors = authors or {}
    out = []
    for r in items:
        m, c = r.machine or {}, r.context or {}
        pages = m.get('plan_pages') or m.get('sheets') or []
        out.append([
            r.created_at.strftime('%d.%m.%Y %H:%M') if r.created_at else '',
            c.get('project', ''), c.get('cipher') or c.get('document', ''),
            c.get('section_label') or c.get('section', ''),
            'сверка' if r.source == 'match' else 'паспорт тома',
            r.subject, m.get('status') or m.get('text', ''),
            m.get('spec_qty'), m.get('plan_qty'), m.get('schema_qty'),
            ', '.join(str(p) for p in pages[:12]),
            reason_label(r.reason), r.comment, authors.get(r.author_id, ''),
        ])
    return out
