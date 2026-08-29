# -*- coding: utf-8 -*-
"""Замечания: решение эксперта по расхождению и формулировка для бюро.

Машина находит расхождение и предлагает формулировку — принимает решение и
подписывает его человек. Формулировка сразу пишется так, как её прочтёт
бюро: что заявлено, что нашли, что просим сделать. Эксперт правит текст
руками, и правка не теряется при следующем прогоне.

Ключ замечания не зависит от id строк: строки сверки переписываются
каждым прогоном, а решение обязано его пережить.
"""
import hashlib
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import models
from .models import Document, MatchItem, Remark

STATUS_LABELS = {
    models.OPEN: 'в работе',
    models.DISMISSED: 'снято',
    models.SENT: 'передано бюро',
}

# что проверялось — те же названия, что в выгрузке паспорта
PASSPORT_TITLES = {
    'sheet_gap': 'Нумерация ведомости чертежей',
    'ref_missing': 'Прилагаемый документ не сдан',
    'ref_cipher': 'Расхождение шифров документа',
    'spec_sheets': 'Число листов спецификации',
    'revision_mismatch': 'Номера изменений',
    'norm': 'Нормативная база',
    'symbol_unused': 'Условные обозначения',
    'unreadable': 'Читаемость листов',
}


def _now():
    return datetime.now(timezone.utc)


def match_key(item) -> str:
    """Устойчивый ключ строки сверки: вид и каноническая марка.

    Каноническая марка не меняется от прогона к прогону — в отличие от id
    строки, которую пересверка пересоздаёт.
    """
    return f'm:{item.kind}:{item.mark}'[:80]


def passport_key(finding) -> str:
    """Ключ расхождения паспорта: код проверки и отпечаток текста.

    Текст входит в ключ намеренно: «пропущены листы 4, 12» и «пропущен
    лист 4» — разные замечания, хотя код проверки один.
    """
    code = finding.get('code', '')
    digest = hashlib.sha1((finding.get('text') or '').encode('utf-8')).hexdigest()[:10]
    return f'p:{code}:{digest}'[:80]


def _num(v):
    """Числа в формулировке — как их прочтёт человек: 10020, а не 10020.0."""
    if v is None:
        return '—'
    return str(int(v)) if float(v).is_integer() else f'{v:.2f}'.rstrip('0').rstrip('.')


def match_text(item) -> str:
    """Предложенная формулировка по строке сверки.

    Пишется от лица отдела: что в спецификации, что нашли на чертежах,
    что просим сделать. Эксперт правит её руками — это черновик, а не
    окончательный текст письма.
    """
    mark = ' · '.join(item.marks or []) or item.mark
    name = (item.names or '').split(' | ')[0]
    unit = item.unit or 'шт.'
    spec = _num(item.spec_qty)
    head = f'{mark} — {name}. По спецификации {spec} {unit}'
    status = item.status or ''

    if status.startswith('нет на чертежах'):
        return (f'{head}; на планах и схемах тома обозначение не встречается. '
                'Просим нанести оборудование на чертежи или исключить позицию '
                'из спецификации.')
    if status.startswith('есть на чертежах, метраж не подписан'):
        return (f'{head}; на чертежах марка встречается, но длины не подписаны — '
                'проверить объём по чертежам невозможно. Просим указать длины '
                'участков или приложить кабельный журнал.')
    if status.startswith('узел'):
        return (f'{head}; марка встречается только на листах типовых узлов, '
                'где подписан состав одного узла. Просим указать общее '
                'количество по объекту.')
    found = []
    if item.plan_qty:
        found.append(f'по планам {_num(item.plan_qty)}')
    if item.schema_qty:
        found.append(f'по схемам {_num(item.schema_qty)}')
    if item.exact_qty:
        src = f' ({item.source})' if item.source else ''
        found.append(f'по точному источнику{src} {item.exact_qty}')
    tail = ', '.join(found) or 'на чертежах количество не определяется'
    if status.startswith('ок (частично)'):
        return (f'{head}; {tail} — сходится не по всем чертежам. Просим '
                'проверить полноту нанесения оборудования.')
    return (f'{head}; {tail}. Просим устранить расхождение между '
            'спецификацией и чертежами.')


def match_evidence(item) -> str:
    parts = [f'статус сверки: {item.status}']
    if item.source:
        parts.append(f'источник: {item.source}')
    if item.spec_pages:
        parts.append('листы спецификации: '
                     + ', '.join(str(p) for p in item.spec_pages))
    if item.plan_pages:
        parts.append('планы: ' + ', '.join(str(p) for p in item.plan_pages[:12]))
    if item.schema_pages:
        parts.append('схемы: ' + ', '.join(str(p) for p in item.schema_pages[:12]))
    return '; '.join(parts)


def by_key(session, document_id):
    return {r.key: r for r in session.scalars(
        select(Remark).where(Remark.document_id == document_id)).all()}


def items(session, document_id):
    """Замечания тома: сначала в работе, потом снятые."""
    order = {models.OPEN: 0, models.SENT: 1, models.DISMISSED: 2}
    rows = session.scalars(
        select(Remark).where(Remark.document_id == document_id)).all()
    return sorted(rows, key=lambda r: (order.get(r.status, 3),
                                       r.level != 'red', r.subject or ''))


def stats(rows):
    return {'total': len(rows),
            'open': sum(1 for r in rows if r.status == models.OPEN),
            'sent': sum(1 for r in rows if r.status == models.SENT),
            'dismissed': sum(1 for r in rows if r.status == models.DISMISSED)}


def decide(session, doc, key, status, source='match', subject='', text='',
           evidence='', sheets=(), level='red', user_id=None):
    """Создать или изменить решение по расхождению.

    Формулировку, once правленную экспертом, повторное нажатие не затирает:
    менять решение и переписывать текст — разные действия.
    """
    remark = session.scalar(select(Remark).where(Remark.document_id == doc.id,
                                                 Remark.key == key))
    if remark is None:
        remark = Remark(org_id=doc.org_id, document_id=doc.id, source=source,
                        key=key, subject=subject[:300], text=text,
                        evidence=evidence, sheets=list(sheets), level=level)
        session.add(remark)
    remark.status = status
    remark.author_id = user_id
    remark.decided_at = _now()
    try:
        session.commit()
    except IntegrityError:
        # двойной клик по кнопке: замечание уже завели параллельным запросом.
        # Уникальный индекс по (том, ключ) не даёт появиться второму — просто
        # берём существующее и доводим до нужного статуса
        session.rollback()
        remark = session.scalar(select(Remark).where(Remark.document_id == doc.id,
                                                     Remark.key == key))
        if remark is None:
            raise
        remark.status = status
        remark.author_id = user_id
        remark.decided_at = _now()
        session.commit()
    return remark


def edit(session, remark, text=None, status=None, user_id=None):
    if text is not None:
        remark.text = text.strip()
    if status is not None:
        remark.status = status
    remark.author_id = user_id or remark.author_id
    remark.decided_at = _now()
    session.commit()
    return remark


def from_match(session, doc, item, status, user_id=None):
    return decide(session, doc, match_key(item), status, source='match',
                  subject=(' · '.join(item.marks or []) or item.mark),
                  text=match_text(item), evidence=match_evidence(item),
                  sheets=item.plan_pages or item.schema_pages or item.spec_pages,
                  level=item.level, user_id=user_id)


def from_passport(session, doc, finding, status, user_id=None):
    code = finding.get('code', '')
    return decide(session, doc, passport_key(finding), status, source='passport',
                  subject=PASSPORT_TITLES.get(code, code),
                  text=finding.get('text', ''),
                  evidence=f'проверка паспорта тома: {code}',
                  sheets=finding.get('sheets') or [],
                  level=finding.get('level', 'red'), user_id=user_id)


def orphaned(session, document_id):
    """Замечания сверки, чьей строки в свежем прогоне больше нет.

    Такое бывает по делу: бюро исправило чертёж, и расхождение ушло.
    Молча удалять их нельзя — эксперт должен увидеть, что замечание
    закрылось само.
    """
    live = {f'm:{i.kind}:{i.mark}' for i in session.scalars(
        select(MatchItem).where(MatchItem.document_id == document_id)).all()}
    return [r for r in session.scalars(
        select(Remark).where(Remark.document_id == document_id,
                             Remark.source == 'match')).all()
            if r.key not in live]


def document_of(session, document_id):
    return session.get(Document, document_id)
