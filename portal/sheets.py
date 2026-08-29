# -*- coding: utf-8 -*-
"""Просмотрщик листа: картинки, метки, замечания на листе.

Картинки листов рисует воркер после приёмки. Здесь — раздача из
хранилища и дорисовка того, чего там ещё нет: пока фоновая задача идёт,
эксперт уже листает том, и «лист не готов» было бы враньём — лист
готовится за две десятых секунды.

Единственное место, где веб трогает PDF. Ограничения явные: бюджет
пикселей внутри `agent.render` и семафор на два одновременных рендера —
иначе десяток одновременных зумов положит веб по памяти.
"""
import logging
import threading

from sqlalchemy import select

from . import models, remarks as remark_service
from .models import MatchItem, Remark, Sheet
from .pdfcache import local_path
from .storage import crop_key, get_storage, page_key

log = logging.getLogger(__name__)

RENDER_SLOTS = threading.Semaphore(2)

KIND_LABELS = {
    'plan': 'план', 'schema': 'схема', 'spec': 'спецификация',
    'vt': 'ведомость', 'general': 'общие данные', 'appendix': 'приложение',
    'cover': 'титул', 'other': 'лист',
}


def _render(doc, page, kind, width=None, box=None):
    """Нарисовать и положить в хранилище. -> байты png."""
    from agent.render import page_crop, page_image, THUMB_WIDTH
    path = local_path(doc.id, doc.file_key)
    with RENDER_SLOTS:
        if box is not None:
            im = page_crop(path, page, box, width=width or 1600)
        elif kind == 'thumb':
            im = page_image(path, page, width=THUMB_WIDTH)
        else:
            im = page_image(path, page)
    return im.png


def image(doc, page: int, kind: str = 'overview') -> bytes:
    """Обзор или миниатюра листа: из хранилища, а если там пусто — рисуем."""
    storage = get_storage()
    key = page_key(doc.id, page, kind)
    try:
        return storage.read_bytes(key)
    except Exception:
        pass
    png = _render(doc, page, kind)
    try:
        storage.put_bytes(key, png, 'image/png')
    except Exception:
        log.exception('картинка листа %s тома %s не сохранена', page, doc.id)
    return png


def crop(doc, page: int, box, width: int = 1600) -> bytes:
    """Кроп под зум. Кэшируется: в ту же область эксперт вернётся не раз."""
    storage = get_storage()
    key = crop_key(doc.id, page, box, width)
    try:
        return storage.read_bytes(key)
    except Exception:
        pass
    png = _render(doc, page, 'crop', width=width, box=box)
    try:
        storage.put_bytes(key, png, 'image/png')
    except Exception:
        pass
    return png


def match_anchors(session, item: MatchItem):
    """Где марка подписана на листах тома. Считается один раз и лениво.

    Поиск идёт по листам, на которых сверка её уже встретила, — значит
    хотя бы одно вхождение там точно есть, и пустой ответ означает, что
    марка попала в текст листа не подписью, а, например, в таблице.
    """
    if item.anchors:
        return item.anchors
    pages = list(dict.fromkeys((item.plan_pages or []) + (item.schema_pages or [])))
    if not pages:
        return []
    from agent.render import find_marks
    doc = session.get(models.Document, item.document_id)
    if doc is None or not doc.file_key:
        return []
    marks = [m for m in (item.marks or []) if m] or [item.mark]
    path = local_path(doc.id, doc.file_key)
    found = []
    with RENDER_SLOTS:
        for page in pages[:12]:
            for mark, rects in find_marks(path, page, marks).items():
                for r in rects:
                    found.append(dict(r, page=page, mark=mark))
    item.anchors = found
    session.commit()
    return found


def remark_pages(session, doc):
    """Сколько замечаний относится к каждому листу тома.

    Замечание из сверки знает листы, на которых машина встретила марку,
    даже если метку на чертеже никто не ставил, — иначе найти лист,
    к которому относится найденное машиной, было бы нечем.
    """
    counts = {}
    for r in remark_service.items(session, doc.id):
        if r.status == models.DISMISSED:
            continue
        for page in remark_service.pages_of(r):
            counts[page] = counts.get(page, 0) + 1
    return counts


def pages(session, doc, only_marked=False):
    """Листы тома для полосы: номер, тип, заголовок, число замечаний."""
    rows = session.scalars(
        select(Sheet).where(Sheet.document_id == doc.id)
        .order_by(Sheet.page)).all()
    counts = remark_pages(session, doc)
    out = [{'page': s.page, 'kind': s.kind,
            'label': KIND_LABELS.get(s.kind, s.kind),
            'title': s.title or '', 'remarks': counts.get(s.page, 0)}
           for s in rows]
    if only_marked:
        out = [s for s in out if s['remarks']] or out
    return out


def page_remarks(session, doc, page):
    """Замечания листа: и поставленные меткой, и просто относящиеся к нему."""
    return [r for r in remark_service.items(session, doc.id)
            if page in remark_service.pages_of(r)]


def _place_found(session, doc, remarks, page):
    """Подобрать координаты замечаниям сверки, у которых метки ещё нет.

    Замечание завели из таблицы — координат там взяться неоткуда. Зато
    здесь, на открытом листе, известно, где марка подписана: ставим метку
    один раз и дальше ведём себя как с обычной.
    """
    pending = [r for r in remarks
               if r.source == 'match' and not r.anchor and r.page == page]
    if not pending:
        return
    for r in pending:
        kind, _, mark = (r.key or '').partition(':')[2].partition(':')
        item = session.scalar(select(MatchItem).where(
            MatchItem.document_id == doc.id, MatchItem.kind == kind,
            MatchItem.mark == mark))
        if item is None:
            continue
        try:
            found = match_anchors(session, item)
        except Exception:
            # подбор координат — удобство, а не обязанность: если файл не
            # достался, лист всё равно должен открыться
            log.exception('не удалось подобрать метку для замечания %s', r.id)
            return
        anchor = next((a for a in found if a.get('page') == page), None)
        if anchor is None:
            continue
        r.anchor = {'kind': 'mark', 'x': anchor['x'], 'y': anchor['y'],
                    'w': anchor['w'], 'h': anchor['h']}
        r.anchor_document_id = doc.id
        r.anchor_label = f'л. {page}, {anchor.get("mark", "") or r.subject}'[:120]
    session.commit()


def context(session, doc, page=0, mark_item=None, only_marked=False, focus=None):
    """Всё, что показывает вкладка «Лист»."""
    sheets = pages(session, doc, only_marked)
    numbers = [s['page'] for s in sheets]
    if page not in numbers:
        with_remarks = next((s['page'] for s in sheets if s['remarks']), None)
        page = (with_remarks
                or next((s['page'] for s in sheets if s['kind'] == 'plan'),
                        numbers[0] if numbers else 1))
    item = session.get(MatchItem, mark_item) if mark_item else None
    highlights = []
    if item is not None:
        highlights = [a for a in match_anchors(session, item)
                      if a.get('page') == page]
        if not highlights:
            first = next((a for a in (item.anchors or []) if a.get('page')), None)
            if first and first['page'] in numbers:
                page = first['page']
                highlights = [a for a in item.anchors if a.get('page') == page]
    remarks = page_remarks(session, doc, page)
    _place_found(session, doc, remarks, page)
    return {
        'doc': doc, 'page': page, 'sheets': sheets,
        'sheet': next((s for s in sheets if s['page'] == page), None),
        'remarks': remarks, 'focus': focus,
        'unplaced': [r for r in remarks if not r.anchor],
        'item': item, 'highlights': highlights,
        'only_marked': only_marked,
        'marked_pages': sum(1 for s in sheets if s['remarks']),
        'ready': doc.pages_rendered or 0,
        'total': doc.pages_total or len(sheets),
    }
