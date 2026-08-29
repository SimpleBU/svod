# -*- coding: utf-8 -*-
"""Флаги готовности тома к проверке.

Отвечают эксперту на вопрос «насколько машине можно верить по этому тому».
Правило: тревожных отметок ровно столько, сколько реальных проблем.
Флаг показывается только там, где он осмыслен для раздела: отсутствие
кабельного журнала в томе отопления — не новость, а шум.
"""
GREEN, AMBER, RED = 'g', 'y', 'r'
LEVEL_ORDER = {GREEN: 0, AMBER: 1, RED: 2}

CABLE_SECTIONS = ('ЭОМ', 'ЭМ', 'ЭС', 'ЭН', 'СС', 'АСУД', 'АУПС', 'АК',
                  'СПСиА', 'АУГПТ')
LIGHT_SECTIONS = ('ЭОМ', 'ЭО')
PIPE_SECTIONS = ('ОВ', 'ОВК', 'ВК', 'НВК', 'ТМ')


def _in(section, group):
    # обе стороны в верхнем регистре: шифр раздела бывает смешанным (СПСиА)
    s = (section or '').upper()
    return any(s == g.upper() or s.startswith(g.upper()) and s[len(g):].isdigit()
               for g in group)


def document_flags(caps, section=''):
    """caps — словарь Capabilities из фасада. -> [{'label','level'}]"""
    caps = caps or {}
    out = []
    if caps.get('has_spec'):
        out.append({'label': 'Спецификация найдена', 'level': GREEN})
    else:
        out.append({'label': 'Спецификации нет — сверка невозможна', 'level': RED})

    if _in(section, CABLE_SECTIONS):
        out.append({'label': 'Кабельный журнал есть', 'level': GREEN}
                   if caps.get('has_cable_journal') else
                   {'label': 'Кабельного журнала нет — метраж с допуском', 'level': AMBER})
    if _in(section, LIGHT_SECTIONS):
        out.append({'label': 'Ведомость освещения есть', 'level': GREEN}
                   if caps.get('has_lighting_list') else
                   {'label': 'Ведомости освещения нет', 'level': AMBER})
    if _in(section, PIPE_SECTIONS):
        out.append({'label': 'Геометрия измерима', 'level': GREEN}
                   if caps.get('has_vector_geometry') else
                   {'label': 'Геометрия не измеряется — метраж только по подписям',
                    'level': AMBER})

    bad = caps.get('unreadable_font_pages') or []
    if bad:
        out.append({'label': f'{len(bad)} {_sheets(len(bad))} нечитаемы — смотреть глазами',
                    'level': RED})
    return out


def _sheets(n):
    if n % 10 == 1 and n % 100 != 11:
        return 'лист'
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return 'листа'
    return 'листов'


def readiness(flags):
    """Худший уровень среди флагов — цвет полоски тома."""
    if not flags:
        return GREEN
    return max((f['level'] for f in flags), key=lambda l: LEVEL_ORDER[l])
