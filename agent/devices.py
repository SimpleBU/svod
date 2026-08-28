# -*- coding: utf-8 -*-
"""Аппараты защиты ЭОМ: сопоставление артикула спецификации с записью на схемах.

В спецификации: «ВА105-1P-010A-B», «ДИФ-103-2Р-16А-30мА-C».
На принципиальной схеме то же устройство подписано серией и параметрами
отдельными строками: «ВА-105 1Р» / «In=10А» / «Ir=B10А» / «Idn=30мА».
Ключ сопоставления — (серия, полюсность, номинал, характеристика, уставка УЗО).
"""
import re
from collections import defaultdict

from .match import page_multiplier

# нормализация похожих кириллических/латинских букв
_TR = str.maketrans({'Р': 'P', 'р': 'p', 'А': 'A', 'а': 'a', 'С': 'C', 'с': 'c',
                     'В': 'B', 'Е': 'E', 'е': 'e', 'М': 'M', 'О': 'O', 'о': 'o',
                     'Т': 'T', 'Н': 'H', 'К': 'K', 'Х': 'X'})

SERIES = r'(ВА|ДИФ|ВН|АВДТ|АД)'
# «ВА105-1P-010A-B», «ВА-333Е-3Р-32А», «ВН102-3Р-63А»
SPEC_PAT = re.compile(
    SERIES + r'[\s\-]*(\d{2,3}[A-ZА-Я]?)[\s\-]+(\d)\s*[PР][\s\-]+0*(\d{1,4})\s*[AА]'
    r'(?:[\s\-]+([BCDBСД])\b)?', re.I)
# на схеме: заголовок «ВА-105 1Р» / «ДИФ-103 2Р»
DEV_HEAD = re.compile(SERIES + r'[\s\-]*(\d{2,3}[A-ZА-Я]?)\s+(\d)\s*[PР]\b', re.I)
# компактная запись без полюсности: «ВА-103М» / «D20А» на следующей строке
DEV_HEAD2 = re.compile(SERIES + r'[\s\-]*(\d{2,3}[A-ZА-Я]?)\s*$', re.I)
CHAR_AMP = re.compile(r'^([BCDВСД])\s*(\d{1,4})\s*[AА]$', re.I)
IN_PAT = re.compile(r'In\s*=\s*(\d{1,4})\s*[AА]', re.I)
IR_PAT = re.compile(r'Ir\s*=\s*([BCDВСД])\s*(\d{1,4})\s*[AА]', re.I)
IDN_PAT = re.compile(r'I?[dD∆Δ]n\s*=\s*(\d{2,4})\s*мА', re.I)


def _norm(s):
    return (s or '').translate(_TR).upper().replace(' ', '')


def spec_device_key(mark):
    """Ключ аппарата из марки спецификации, иначе None."""
    m = SPEC_PAT.search(mark or '')
    if not m:
        return None
    series, num, poles, amps, char = m.groups()
    return (_norm(series) + _norm(num), int(poles), int(amps),
            _norm(char) if char else '')


def _device_blocks(lines):
    """Блоки «заголовок аппарата + до 5 строк параметров» на листе схемы."""
    for i, line in enumerate(lines):
        m = DEV_HEAD.match(line.strip())
        if not m:
            # компактный формат: «ВА-103М» и следом «D6А»
            m2 = DEV_HEAD2.match(line.strip())
            nxt = CHAR_AMP.match(lines[i + 1].strip()) if m2 and i + 1 < len(lines) else None
            if nxt:
                series, num = m2.groups()
                # полюсность в компактной записи не указывается
                yield (_norm(series) + _norm(num), 0,
                       int(nxt.group(2)), _norm(nxt.group(1)))
            continue
        series, num, poles = m.groups()
        tail = ' '.join(lines[i + 1:i + 6])
        amps = IN_PAT.search(tail)
        ir = IR_PAT.search(tail)
        # выключатель нагрузки (ВН) характеристики Ir не имеет: если она
        # попала в хвост, то относится к соседнему аппарату
        if _norm(series) == 'BH':
            ir = None
        if not amps and not ir:
            continue
        char = _norm(ir.group(1)) if ir else ''
        val = int(ir.group(2)) if ir else int(amps.group(1))
        yield (_norm(series) + _norm(num), int(poles), val, char)


def count_devices(doc, page_infos):
    """Подсчёт аппаратов на схемах: ключ -> количество, и листы."""
    counts = defaultdict(float)
    detail = defaultdict(set)
    for pi in page_infos:
        lines = [l.strip() for l in doc[pi['page'] - 1].get_text().splitlines()]
        mult = page_multiplier(pi)
        for key in _device_blocks(lines):
            counts[key] += mult
            detail[key].add(pi['page'])
    return dict(counts), {k: sorted(v) for k, v in detail.items()}


def match_spec_devices(counts, detail, spec_marks):
    """Сопоставляет ключи спецификации с найденными на схемах.

    spec_marks: canon_mark -> исходная марка.
    Возвращает canon_mark -> (количество, листы).
    Если характеристика на схеме не указана, ключи без неё тоже засчитываются.
    """
    out = {}
    for canon, raw in spec_marks.items():
        key = spec_device_key(raw)
        if not key:
            continue
        total = counts.get(key, 0)
        pages = list(detail.get(key, []))
        if not total:  # на схеме характеристика могла не указываться
            alt = (key[0], key[1], key[2], '')
            total = counts.get(alt, 0)
            pages = list(detail.get(alt, []))
        # компактная запись без полюсности («ВА-103М» / «D6А»)
        compact = (key[0], 0, key[2], key[3])
        if compact in counts:
            total += counts[compact]
            pages += detail.get(compact, [])
        if total:
            out[canon] = (total, sorted(set(pages)))
    return out
