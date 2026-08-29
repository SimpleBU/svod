# -*- coding: utf-8 -*-
"""Проверки распознавания нормативной базы.

Запуск:  python -m agent.tests_norms

Проверки нарочно не привязаны к статусам из реестра: реестр ведёт эксперт,
он будет меняться, и тест не должен падать от каждой правки. Проверяется
то, что зависит от кода: что распознаётся, что не распознаётся и откуда
берётся.
"""
import os

from .general import general
from .norms import extract, norms, problems, load_registry, FROM_REFS

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'Example')

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


def codes(items):
    return {r.code for r in items}


def run():
    # --- ложные срабатывания: «СП» внутри слов, «СПДС» без номера
    junk = ('спецификация оборудования, стояк на плане, СПДС, Стоянки автомобилей, '
            'расположенный, способность, спецификациях')
    check(not extract(junk), f'мусорные срабатывания: {extract(junk)}')

    # --- настоящие обозначения
    text = ('- СП 484.1311500.2020 "Системы пожарной сигнализации"; '
            '- ГОСТ Р 21.101-2020 СПДС; - ГОСТ 21.210-2014; '
            '- Федеральный закон от 22.07.08г. № 123-ФЗ; '
            '- Постановление правительства РФ от 16 сентября 2020 года N1479; '
            '- Правила устройства электроустановок (изд.6,7);')
    got = {c for c, _ in extract(text)}
    for want in ('СП 484.1311500.2020', 'ГОСТ Р 21.101-2020', 'ГОСТ 21.210-2014',
                 '№ 123-ФЗ', 'Постановление Правительства РФ № 1479', 'ПУЭ'):
        check(want in got, f'не распознано: {want} (получено {sorted(got)})')

    # --- «СП 52.13330-2016» и «СП 52.13330.2016» — один документ
    reg = load_registry()
    a = norms('- СП 52.13330-2016 Естественное и искусственное освещение', registry=reg)
    check(len(a) == 1 and a[0].status != 'unknown',
          'написание через дефис не сопоставилось с реестром')

    # --- упоминание внутри названия другого документа не считается замечанием
    ctx = norms('- СП 51.13330.2011 "Защита от шума". '
                'Актуализированная редакция СНиП 23-03-2003;', registry=reg)
    snip = next((r for r in ctx if r.code.startswith('СНиП')), None)
    check(snip is not None and snip.contextual,
          'СНиП внутри названия свода правил не помечен как контекстный')
    check(not any(r.code.startswith('СНиП') for r in problems(ctx)),
          'СНиП внутри названия свода правил попал в замечания')
    check(not any(r.code.startswith('СП') and r.contextual for r in ctx),
          'сам свод правил ошибочно помечен контекстным')

    # --- реальные тома
    cases = {
        'ПР-01.24-1-ЭОМ (Изм. 1-4).pdf': ('СП 6.13130.2021', 'ГОСТ Р 21.101-2020',
                                          'ГОСТ Р 50571.5.54-2024'),
        'ПР-01.24-3-ПРК с изм.1,2.pdf': ('СП 30.13330.2020', 'ГОСТ 3262-75'),
        'ПР-0124-1-ОВ1_изм.1,2.pdf': ('СП 60.13330.2020', 'СП 73.13330.2016'),
        'ПР-01_24-АСУД (Изм.1).pdf': ('ВСН 60-89', 'СП 31-110-2003'),
    }
    for name, want in cases.items():
        path = os.path.join(BASE, name)
        if not os.path.exists(path):
            print(f'  пропущен (нет файла): {name}')
            continue
        g = general(path)
        items = norms(g.guidelines_text, g.refs)
        have = codes(items)
        for w in want:
            check(w in have, f'{name}: не найден {w}')
        unknown = [r.code for r in items if not r.known]
        check(not unknown, f'{name}: нет в реестре {unknown} — дополнить norms_registry.yaml')

        # в АСУД нормативы объявлены ведомостью ссылочных, а не текстом указаний
        if 'АСУД' in name:
            check(any(FROM_REFS in r.sources for r in items),
                  'АСУД: нормативы из ведомости ссылочных не подхвачены')

    if FAILS:
        print('ОШИБКИ:')
        for f in FAILS:
            print(' -', f)
    else:
        print('agent.norms: все проверки пройдены')
    return 1 if FAILS else 0


if __name__ == '__main__':
    raise SystemExit(run())
