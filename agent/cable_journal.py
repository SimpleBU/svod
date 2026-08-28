# -*- coding: utf-8 -*-
"""Кабельный журнал (.КЖ) как источник длин кабелей для разделов ЭОМ/слаботочки.

Лист журнала: Маркировка | Трасса | Участок | Кабель (Марка | Кол-во и сечение |
Длина, м). Суммирует длины по ключу «марка + сечение», учитывая множитель
кратности «N(...)» и зачёркнутые изменениями строки.
"""
import re
from collections import defaultdict

import pymupdf as fitz

from .extract import _hlines, _is_struck
from .match import norm_text, cable_key, spec_cable_key

JOURNAL_CODE = re.compile(r'ПР-[\d/._\-]+-\w+\.КЖ')
# «1(3×1,5(N,PE)-0,66)» -> кратность 1, сечение «3×1,5(N,PE)-0,66»
CORE_PAT = re.compile(r'^\s*(\d+)?\s*\((.+)\)\s*$')


def is_journal_page(page):
    text = page.get_text()
    return bool(JOURNAL_CODE.search(text)) and 'Длина' in text


def journal_pages(doc):
    return [i + 1 for i in range(len(doc)) if is_journal_page(doc[i])]


def _header_map(row):
    """Индексы колонок Кабель/по проекту: марка, сечение, длина."""
    idx = {}
    for i, c in enumerate(row):
        t = (c or '').replace('\n', ' ').strip()
        if t == 'Марка' and 'mark' not in idx:
            idx['mark'] = i
        elif t.startswith('Количество ка') and 'core' not in idx:
            idx['core'] = i
        elif t.startswith('Длина') and 'len' not in idx:
            idx['len'] = i
    return idx if {'mark', 'core', 'len'} <= set(idx) else None




def parse_journal(doc, pages=None):
    """Суммы длин по кабелям: key -> метры. Также возвращает detail по листам."""
    pages = pages or journal_pages(doc)
    sums = defaultdict(float)
    detail = defaultdict(set)
    for pno in pages:
        page = doc[pno - 1]
        words = page.get_text('words')
        hl = _hlines(page)
        rot = page.rotation_matrix  # координаты ячеек — без учёта /Rotate
        tabs = page.find_tables()
        for tab in tabs.tables:
            rows = tab.extract()
            hdr = None
            for ri, row in enumerate(rows):
                if hdr is None:
                    hdr = _header_map(row)
                    continue
                def cell(key):
                    i = hdr.get(key)
                    return (row[i] or '').replace('\n', ' ').strip() if i is not None and i < len(row) else ''
                mark, core, ln = cell('mark'), cell('core'), cell('len')
                if not mark or not core or not ln:
                    continue
                m = CORE_PAT.match(core)
                mult = int(m.group(1) or 1) if m else 1
                section = m.group(2) if m else core
                nums = re.findall(r'\d+(?:[.,]\d+)?', ln)
                if not nums:
                    continue
                # зачёркнутые изменениями значения длины отбрасываем
                try:
                    bb = fitz.Rect(tab.rows[ri].cells[hdr['len']])
                    cw = [w for w in words
                          if bb.contains(
                              fitz.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2) * rot)]
                    live = [w[4] for w in cw if not _is_struck(w, hl)]
                    if cw and not live:
                        continue
                    if live:
                        nums = re.findall(r'\d+(?:[.,]\d+)?', ' '.join(live))
                except (IndexError, TypeError, KeyError):
                    pass
                if not nums:
                    continue
                val = float(nums[-1].replace(',', '.'))
                key = cable_key(mark, section)
                sums[key] += val * mult
                detail[key].add(pno)
    return dict(sums), {k: sorted(v) for k, v in detail.items()}


