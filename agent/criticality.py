# -*- coding: utf-8 -*-
"""Критичность позиций спецификации: что эксперт будет проверять.

Спецификация тома — 50–500 строк, руками проверяют 30–50. Машина не решает,
что важно: она сортирует и объясняет причину, а отбирает человек галочками.
Поэтому у каждой позиции рядом с баллом лежат «почему» человеческим текстом
и «чем проверяется» — без этого галочка бессмысленна.

Балл складывается из сигналов (веса в SIGNALS). Самый весомый — упоминание
позиции в регистрации изменений: бюро само сказало, что здесь что-то
поменялось. Самый отрицательный — крепёж и расходники: саморез, изменённый
изменением, наверх подниматься не должен.

Функция чистая: PDF не открывается, работает по уже разобранным данным.
Пересчёт после правки порогов — секунды, а не повторный разбор тома.
LLM не используется.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict

# --------------------------------------------------------------- сигналы

CHANGED = 'changed'
IN_LEGEND = 'in_legend'
LENGTH = 'length'
BULK = 'bulk'
HEAD = 'head'
VAGUE = 'vague'
FASTENER = 'fastener'
EXCLUDED = 'excluded'

SIGNALS = {
    CHANGED:  (40, 'затронута изменением'),
    IN_LEGEND: (20, 'есть в условных обозначениях'),
    LENGTH:   (15, 'метраж'),
    BULK:     (15, 'топ по объёму'),
    HEAD:     (10, 'головное оборудование'),
    VAGUE:    (10, 'строка неоднозначна'),
    FASTENER: (-25, 'крепёж или расходник'),
    EXCLUDED: (-15, 'не поставляется по проекту'),
}

CLASS_A, CLASS_B, CLASS_C = 'A', 'B', 'C'
# Пороги откалиброваны на четырёх реальных томах (ЭОМ 601 строка, ПРК 730,
# ОВ1 688, СПСиА 53). При 55 том ПРК не получал ни одной позиции класса A.
THRESHOLD_A = 40
THRESHOLD_B = 20

# Верхняя граница предложения. Балла мало: в томе ОВ1 бюро расписало
# изменения так подробно, что «затронута изменением» срабатывает у трети
# спецификации — и класс A разрастается до 260 позиций, которые никто
# не проверит. Машина предлагает столько, сколько человек успевает.
MAX_PROPOSED = 50
MAX_SHARE = 0.10
MIN_PROPOSED = 10

BULK_QUANTILE = 0.9      # верхние 10 % по количеству внутри своей единицы

LENGTH_UNITS = ('м', 'пм', 'погм', 'мп', 'п.м')

HEAD_WORDS = ('прибор приемно-контрольный', 'приемно-контрольн', 'шкаф', 'щит',
              'вру', 'грщ', 'уэрм', 'цпиу', 'пульт', 'блок индикации',
              'источник вторичного', 'источник бесперебойного', 'ибп',
              'станция', 'насос', 'вентилятор', 'панель распределительн')

FASTENER_WORDS = ('саморез', 'дюбель', 'анкер', 'гайка', 'шайба', 'шпилька',
                  'хомут', 'стяжка', 'винт', 'болт', 'скоба', 'бирка',
                  'маркер', 'изолента', 'лента монтажная', 'колпачок')

# чем проверяется
BY_PLANS = 'по планам'
BY_SCHEMAS = 'по схемам'
BY_JOURNAL = 'по кабельному журналу'
BY_LIGHTING = 'по ведомости освещения'
BY_EYE = 'только глазами'

# --------------------------------------------------------- морфология «на глаз»

# Полноценный стеммер сюда не нужен: сравниваются короткие технические
# наименования. Срезаем частые окончания — «саморезов» и «Саморез»,
# «держателей» и «Держатель», «трубы» и «труба» должны совпасть.
ENDINGS = ('ами', 'ями', 'ого', 'его', 'ому', 'ему', 'ыми', 'ими', 'ов', 'ев',
           'ей', 'ая', 'яя', 'ое', 'ее', 'ые', 'ие', 'ый', 'ий', 'ой', 'ах',
           'ях', 'ам', 'ям', 'ом', 'ем', 'их', 'ых', 'ую', 'юю', 'у', 'ю',
           'е', 'а', 'ы', 'и', 'я', 'ь', 'о')

STOP = {'для', 'при', 'под', 'над', 'без', 'или', 'мм', 'см', 'шт', 'что',
        'его', 'как', 'так', 'все', 'том', 'изм', 'лист', 'листа', 'дан'}

MIN_WORD = 4        # слова короче в сравнении не участвуют
RARE_STEM = 6       # редкая основа: длинная и встречается в двух-трёх строках
RARE_ROWS = 3


def _norm(s):
    return re.sub(r'\s+', ' ', (s or '')).replace('ё', 'е').strip().lower()


def _stem(word):
    if len(word) <= MIN_WORD:
        return word
    for e in ENDINGS:
        if word.endswith(e) and len(word) - len(e) >= MIN_WORD:
            return word[:-len(e)]
    return word


def stems(text):
    """Основы значимых слов текста."""
    out = set()
    for w in re.findall(r'[а-яa-z]{3,}', _norm(text)):
        if w in STOP:
            continue
        if len(w) >= MIN_WORD:
            out.add(_stem(w))
    return out


def _codes(text):
    """Марки из текста: «РМ-1С», «КСРЭПнг(А)-FRHF», «ШУН/В-1,5-03-R3».

    Требовать в марке цифру нельзя: «КСРЭПнг(А)-FRHF» — марка кабеля целиком
    без единой цифры, цифры стоят отдельным типоразмером. Признак марки —
    латиница, скобка, дробь или дефис внутри слова; обычное русское слово
    ничего из этого не содержит.
    """
    out = set()
    for c in re.findall(r'[A-Za-zА-Яа-яЁё0-9][\w()/,.х×-]{3,}', text or ''):
        c = c.strip('.,;')
        if len(c) >= 4 and re.search(r'[0-9A-Za-z()/-]', c):
            out.add(c)
    return out


def _is_dimension(flat):
    """«1х2х1,13», «4х30» — типоразмер, а не марка.

    По нему нельзя связывать позиции: «Актуализирован метраж кабеля
    КСРЭПнг(А)-FRHF 1х2х1,13» иначе цепляет заодно КСРПнг(А)-FRHF 1х2х1,13 —
    другой кабель с тем же сечением.
    """
    return bool(re.fullmatch(r'[\dхx.,/]+', flat or ''))


def _flat(code):
    return re.sub(r'[\s\-–—_.,()"«»]', '', (code or '')).lower().replace('ё', 'е')


# ------------------------------------------------------------------ модели

@dataclass
class Reason:
    code: str
    text: str
    weight: int


@dataclass
class Evidence:
    kind: str           # revision | legend
    text: str


@dataclass
class CheckRow:
    key: str
    index: int          # номер строки в спецификации, как её разобрали
    pos: str
    name: str
    mark: str
    canon: str
    unit: str
    qty: float | None
    page: int
    score: int
    cls: str
    reasons: list[Reason] = field(default_factory=list)
    verifiable_by: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)

    def as_dict(self):
        return asdict(self)


def position_key(mark, name, unit, pos=''):
    """Устойчивый ключ позиции.

    Решение эксперта хранится по нему, а не по id строки: при повторном
    разборе тома id меняются, а позиция остаётся той же.
    """
    base = _flat(mark) or (_norm(name) + '|' + _norm(pos))
    raw = base + '|' + _norm(name) + '|' + _norm(unit)
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]


# ------------------------------------------------------------- сопоставления

def _sentences(text):
    return [s.strip() for s in re.split(r'(?<=[.;])\s+', text or '') if s.strip()]


def _head_stem(name):
    """Основа первого значимого слова наименования: «Саморез», «Держатель».

    По одному слову позицию можно узнать только если это её главное слово.
    Иначе «установки» из «Исправлены марки шкафов управления установками ПДЗ»
    цепляет «Сигнализатор потока воздуха для уличной установки».
    """
    for w in re.findall(r'[а-яa-z]{3,}', _norm(name)):
        if w not in STOP and len(w) >= MIN_WORD:
            return _stem(w)
    return ''


def _match_changed(row_mark, row_stems, revisions, rare, head=''):
    """Позиция упомянута в регистрации изменений? -> цитата или None.

    Три способа, в порядке убывания надёжности: марка, две общие основы,
    одна редкая основа. Последнее нужно для позиций без марки: «саморезов»
    в тексте изменения и «Саморез 4х30» в спецификации.
    """
    flat_mark = _flat(row_mark)
    for entry in revisions:
        content = getattr(entry, 'content', '') or ''
        for sentence in _sentences(content):
            if flat_mark and len(flat_mark) >= 4:
                for code in _codes(sentence):
                    flat = _flat(code)
                    if len(flat) < 4 or _is_dimension(flat):
                        continue
                    if flat in flat_mark or flat_mark in flat:
                        return sentence
            common = row_stems & stems(sentence)
            if len(common) >= 2:
                return sentence
            if head and head in rare and head in common:
                return sentence
    return None


def _match_legend(row_mark, row_stems, symbols):
    """Позиция есть в таблице условных обозначений? -> подпись символа."""
    flat_mark = _flat(row_mark)
    for s in symbols:
        code = _flat(getattr(s, 'code', ''))
        if code and len(code) >= 3 and flat_mark and (code in flat_mark or flat_mark in code):
            return getattr(s, 'name', '')
        name = getattr(s, 'name', '')
        if len(row_stems & stems(name)) >= 2:
            return name
    return None


def _quantiles(spec):
    """Порог «топ по объёму» отдельно для каждой единицы измерения:
    8480 метров кабеля и 64 штуки реле сравнивать между собой нельзя.

    Крепёж из распределения исключён: тридцать тысяч саморезов задирают
    порог так, что ни одна позиция оборудования до него не дотягивается.
    """
    by_unit = {}
    for row in spec:
        if row.qty and not _has(row.name, FASTENER_WORDS):
            by_unit.setdefault(_norm(row.unit), []).append(row.qty)
    out = {}
    for unit, values in by_unit.items():
        values.sort()
        if len(values) < 5:
            continue
        out[unit] = values[min(len(values) - 1, int(len(values) * BULK_QUANTILE))]
    return out


def _is_length(unit):
    u = _norm(unit).rstrip('.').replace(' ', '')
    return u in LENGTH_UNITS


def _has(text, words):
    """Ключевое слово ищется по основе, а не подстрокой.

    Подстрока даёт тихие ошибки: «щит» находится внутри «огнезащитная»,
    и пена для проходок становится головным оборудованием. Многословные
    ключи («блок индикации») сравниваются как есть.
    """
    low = _norm(text)
    st = stems(text)
    for w in words:
        if ' ' in w:
            if w in low:
                return True
        else:
            ws = _stem(_norm(w))
            if any(s.startswith(ws) for s in st):
                return True
    return False


def _verifiable(row, in_legend, caps, kind_counts):
    """Чем позицию можно проверить на следующем этапе.

    Марка — пропуск в автоматическую проверку: по ней позицию ищут на планах
    и схемах. Без марки остаются только глаза, даже если листы есть — это
    та же граница, что и во вкладке номенклатуры.
    """
    caps = caps or {}
    kinds = kind_counts or {}
    text = ' '.join((row.name, row.mark, getattr(row, 'category', '')))
    searchable = bool((row.mark or '').strip()) or in_legend
    out = []
    if kinds.get('plan') and searchable:
        out.append(BY_PLANS)
    if kinds.get('schema') and searchable:
        out.append(BY_SCHEMAS)
    if caps.get('has_cable_journal') and _is_length(row.unit) and _has(text, ('кабель',)):
        out.append(BY_JOURNAL)
    if caps.get('has_lighting_list') and _has(text, ('светильник', 'светодиодн',
                                                    'осветительн')):
        out.append(BY_LIGHTING)
    return out or [BY_EYE]


# ------------------------------------------------------------------- фасад

def checkplan(spec, revisions=(), symbols=(), caps=None, kind_counts=None) -> list[CheckRow]:
    """План проверки: позиции спецификации, отсортированные по критичности.

    spec — строки из agent.api.intake, revisions и symbols — из agent.general,
    caps и kind_counts — оттуда же, для колонки «чем проверяется».
    """
    row_stems = [stems(r.name) for r in spec]

    # редкая основа — длинная и встречается в двух-трёх строках спецификации:
    # по ней позицию без марки можно узнать в тексте изменения
    freq = {}
    for st in row_stems:
        for s in st:
            freq[s] = freq.get(s, 0) + 1
    rare = {s for s, n in freq.items() if n <= RARE_ROWS and len(s) >= RARE_STEM}

    quant = _quantiles(spec)
    out = []
    for i, row in enumerate(spec):
        signals, evidence = [], []

        quote = _match_changed(row.mark, row_stems[i], revisions, rare,
                               _head_stem(row.name))
        if quote:
            signals.append(CHANGED)
            evidence.append(Evidence('revision', quote))

        legend = _match_legend(row.mark, row_stems[i], symbols)
        if legend:
            signals.append(IN_LEGEND)
            evidence.append(Evidence('legend', legend))

        if _is_length(row.unit):
            signals.append(LENGTH)

        fastener = _has(row.name, FASTENER_WORDS)

        # крепёж не участвует ни в «топ по объёму», ни в «неоднозначна»:
        # тридцать тысяч саморезов без марки — это нормальный саморез,
        # а не подозрительная строка
        limit = quant.get(_norm(row.unit))
        if not fastener and limit is not None and row.qty and row.qty >= limit:
            signals.append(BULK)

        if _has(row.name, HEAD_WORDS) or _has(row.mark, HEAD_WORDS):
            signals.append(HEAD)

        if not fastener and (getattr(row, 'composite', False)
                             or getattr(row, 'expanded_range', False)
                             or row.qty is None or not row.mark.strip()):
            signals.append(VAGUE)

        if fastener:
            signals.append(FASTENER)

        if getattr(row, 'excluded', False):
            signals.append(EXCLUDED)

        score = max(0, min(100, sum(SIGNALS[s][0] for s in signals)))

        out.append(CheckRow(
            key=position_key(row.mark, row.name, row.unit, row.pos),
            index=i, pos=row.pos, name=row.name, mark=row.mark,
            canon=getattr(row, 'canon', ''), unit=row.unit, qty=row.qty,
            page=getattr(row, 'page', 0), score=score, cls=CLASS_C,
            reasons=[Reason(s, SIGNALS[s][1], SIGNALS[s][0]) for s in signals],
            verifiable_by=_verifiable(row, bool(legend), caps, kind_counts),
            evidence=evidence))

    out.sort(key=lambda r: (-r.score, -(r.qty or 0), r.name))
    cap = min(MAX_PROPOSED, max(MIN_PROPOSED, int(len(out) * MAX_SHARE)))
    for i, row in enumerate(out):
        if row.score >= THRESHOLD_A and i < cap:
            row.cls = CLASS_A
        elif row.score >= THRESHOLD_B:
            row.cls = CLASS_B
    return out


def stats(rows):
    """Сводка для шапки экрана: сколько предложено и сколько всего."""
    by_cls = {CLASS_A: 0, CLASS_B: 0, CLASS_C: 0}
    for r in rows:
        by_cls[r.cls] += 1
    return {'total': len(rows), 'proposed': by_cls[CLASS_A], 'by_class': by_cls}


def main():
    """Отладочный запуск: python -m agent.criticality <файл.pdf>"""
    import sys
    from .api import intake
    from .general import general

    path = sys.argv[1]
    r = intake(path)
    g = general(path)
    rows = checkplan(r.spec, g.revisions, g.symbols,
                     r.capabilities.as_dict(), r.kind_counts)
    for row in rows:
        why = ' · '.join(x.text for x in row.reasons) or '—'
        print(f'  {row.cls} {row.score:>3}  {row.name[:38]:38} {row.mark[:24]:24} '
              f'{str(row.qty or ""):>8} {row.unit:5} | {why} | '
              f'{", ".join(row.verifiable_by)}')
    s = stats(rows)
    print(f"  всего {s['total']}, класс A: {s['by_class']['A']}, "
          f"B: {s['by_class']['B']}, C: {s['by_class']['C']}")


if __name__ == '__main__':
    main()
