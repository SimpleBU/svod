# -*- coding: utf-8 -*-
"""Ведомость осветительного оборудования на планах ЭОМ.

Точный источник количества светильников (аналог кабельного журнала):
колонки «Тип | Марка | … | Кол.». Ведомость не выделяется find_tables
на полном листе, поэтому ищется по заголовку и разбирается отдельно.
"""
import re
from collections import defaultdict

import pymupdf as fitz

from .match import canon_mark, page_multiplier

TITLE = 'Ведомость осветительного'


def _title_band(page):
    """Заголовок ведомости (x0, x1, y) в системе таблиц.

    Ищется пара «Ведомость» + «осветительного» на одной строке: слово
    «оборудования» встречается и в названии листа, поэтому не опорное.
    """
    rot = page.rotation_matrix
    hits = [(w[4], fitz.Rect(w[:4]) * rot) for w in page.get_text('words')
            if w[4] in ('Ведомость', 'осветительного')]
    by_line = {}
    for name, r in hits:
        by_line.setdefault(round(r.y0 / 8), []).append((name, r))
    for group in by_line.values():
        names = {n for n, _ in group}
        if {'Ведомость', 'осветительного'} <= names:
            rects = [r for _, r in group]
            return (min(r.x0 for r in rects), max(r.x1 for r in rects),
                    min(r.y0 for r in rects))
    return None


def _frame_width(page, y_title, x_center):
    """Границы таблицы по строке заголовков колонок («Тип …  Кол.»).

    Дешевле разбора векторной графики: на листах формата А1 get_drawings
    возвращает десятки тысяч примитивов и заметно замедляет прогон.
    """
    rot = page.rotation_matrix
    left = right = None
    for w in page.get_text('words'):
        t = w[4].strip().lower().rstrip(',')
        if t not in ('тип', 'кол.', 'кол'):
            continue
        r = fitz.Rect(w[:4]) * rot
        if not (y_title <= r.y0 <= y_title + 250):
            continue
        if t == 'тип' and (left is None or r.x0 < left):
            left = r.x0
        elif t.startswith('кол') and (right is None or r.x1 > right):
            right = r.x1
    if left is None or right is None or right - left < 100:
        return None
    if not (left - 40 <= x_center <= right + 40):
        return None
    return left - 25, right + 25


def _bands(page):
    """Кандидаты-полосы, в которых ищется таблица ведомости."""
    t = _title_band(page)
    if t is None:
        return []
    x0, x1, y0 = t
    y0 -= 10
    xc = (x0 + x1) / 2
    # таблица ведомости следует сразу за заголовком; ограничение высоты
    # ускоряет find_tables на больших листах в разы
    y1 = min(page.rect.height, y0 + 900)
    out = []
    frame = _frame_width(page, y0, xc)
    if frame:
        out.append(fitz.Rect(max(0, frame[0] - 6), y0,
                             min(page.rect.width, frame[1] + 6), y1))
    w = max(x1 - x0, 120)
    for k in (1.6, 3.2):
        out.append(fitz.Rect(max(0, x0 - w * k), y0,
                             min(page.rect.width, x1 + w * k), y1))
    return out


def _header_map(row):
    idx = {}
    for i, c in enumerate(row):
        t = (c or '').replace('\n', ' ').strip().lower()
        if t == 'тип' and 'type' not in idx:
            idx['type'] = i
        elif t == 'марка' and 'mark' not in idx:
            idx['mark'] = i
        elif t.startswith('кол') and 'qty' not in idx:
            idx['qty'] = i
    return idx if {'mark', 'qty'} <= set(idx) else None


def parse_lighting_lists(doc, page_infos):
    """Суммы по маркам светильников из ведомостей на планах.

    Возвращает (sums: canon_mark -> шт, detail: canon_mark -> [листы]).
    Количество умножается на множитель этажей листа («План 3-11 этажа»).
    """
    sums = defaultdict(float)
    detail = defaultdict(set)
    for pi in page_infos:
        page = doc[pi['page'] - 1]
        if TITLE not in page.get_text():
            continue
        mult = page_multiplier(pi)
        tables = []
        for band in _bands(page):
            tables = [t for t in page.find_tables(clip=band).tables
                      if any(_header_map(r) for r in t.extract()[:4])]
            if tables:
                break
        for tab in tables:
            rows = tab.extract()
            hdr = None
            for row in rows:
                if hdr is None:
                    hdr = _header_map(row)
                    continue
                def cell(k):
                    i = hdr.get(k)
                    return (row[i] or '').replace('\n', ' ').strip() if i is not None and i < len(row) else ''
                mark, qty = cell('mark'), cell('qty')
                if not mark or mark in ('-', '—') or not qty:
                    continue
                m = re.fullmatch(r'\d+(?:[.,]\d+)?', qty.replace(' ', ''))
                if not m:
                    continue
                canon = canon_mark(mark)
                if not canon or canon in ('-', 'люстра'):
                    continue
                sums[canon] += float(qty.replace(',', '.')) * mult
                detail[canon].add(pi['page'])
    return dict(sums), {k: sorted(v) for k, v in detail.items()}
