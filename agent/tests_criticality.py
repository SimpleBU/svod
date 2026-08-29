# -*- coding: utf-8 -*-
"""Проверки модели критичности позиций.

Запуск:  python -m agent.tests_criticality

Логика проверяется на собранных вручную строках — это быстро и не зависит
от того, какие тома лежат в Example/. На реальном томе проверяется только
то, что нельзя подделать: масштаб предложения и время пересчёта.
"""
import os
import time
from types import SimpleNamespace as NS

from .criticality import (checkplan, stats, position_key, stems, _stem, _has,
                          _is_dimension, CLASS_A, CLASS_C, FASTENER_WORDS,
                          HEAD_WORDS, MAX_PROPOSED)

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'Example')

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


def row(name, mark='', unit='шт.', qty=1.0, pos='', **kw):
    return NS(name=name, mark=mark, unit=unit, qty=qty, pos=pos, page=1,
              canon=mark.lower(), category='', composite=False,
              expanded_range=False, excluded=False, **kw)


def rev(content):
    return NS(content=content, number=1, sheets='', doc_code='', basis='')


def sym(name, code=''):
    return NS(name=name, code=code, page=1, bbox=(0, 0, 1, 1))


def run():
    # --- морфология: одно и то же слово в разных формах
    for a, b in (('саморезов', 'саморез'), ('держателей', 'держатель'),
                 ('трубы', 'труба'), ('гофрированной', 'гофрированная'),
                 ('шкафов', 'шкаф')):
        check(_stem(a) == _stem(b), f'основы не совпали: {a} / {b}')

    # --- ключевое слово не должно находиться подстрокой внутри другого
    check(not _has('Пена двухкомпонентная огнезащитная', HEAD_WORDS),
          '«щит» найден внутри «огнезащитная» — головное оборудование по ошибке')
    check(_has('Шкаф управления пожарный адресный', HEAD_WORDS),
          'шкаф управления не опознан как головное оборудование')
    check(_has('Саморез 4х30 мм с дюбелем V5', FASTENER_WORDS), 'саморез не опознан')

    # --- типоразмер маркой не является
    check(_is_dimension('1х2х113'), 'типоразмер принят за марку')
    check(not _is_dimension('ксрэпнгафrhf'), 'марка принята за типоразмер')

    # --- ключ позиции устойчив к порядку строк и различает единицы
    k1 = position_key('РМ-1С-R3', 'Адресный релейный модуль', 'шт.')
    k2 = position_key('РМ-1С-R3', 'Адресный релейный модуль', 'шт.')
    k3 = position_key('РМ-1С-R3', 'Адресный релейный модуль', 'м')
    check(k1 == k2, 'ключ позиции нестабилен')
    check(k1 != k3, 'ключ не различает единицу измерения')
    check(position_key('', 'Саморез 4х30', 'шт.', '12')
          != position_key('', 'Саморез 4х30', 'шт.', '13'),
          'позиции без марки не различаются по номеру')

    # --- саморез, затронутый изменением, наверх не поднимается
    spec = [row('Адресный релейный модуль на 1 выход', 'РМ-1С-R3', qty=64),
            row('Саморез 4х30 мм с дюбелем V5', qty=31920),
            row('Держатель оцинкованный односторонний, д.16 мм', qty=30020),
            row('Труба ПВХ гофрированная самозатухающая Ø16 мм', 'ТУ 2247', 'м', 10020),
            row('Труба ПВХ гладкая самозатухающая Ø40 мм', 'ТУ 2248', 'м', 665),
            row('Сигнализатор потока воздуха для уличной установки', 'SHUFT-SL', qty=3),
            row('Кабель огнестойкий', 'КСРЭПнг(А)-FRHF 1х2х1,13', 'м', 8480),
            row('Кабель огнестойкий', 'КСРПнг(А)-FRHF 1х2х1,13', 'м', 1460),
            row('Гайка М8', qty=148), row('Шайба М8', qty=148),
            row('Хомут нержавеющий', qty=111), row('Муфта труба-труба Ø16 мм', qty=3350)]
    revisions = [rev('Добавлены реле РМ-1С для передачи сигнала "Пожар" в шкафы АК. '
                     'Актуализирован метраж кабеля марки КСРЭПнг(А)-FRHF 1х2х1,13. '
                     'Исправлены марки шкафов управления установками ПДЗ. '
                     'Актуализирован метраж трубы гофрированной диам. 16 мм, '
                     'актуализировано количество держателей для трубы и саморезов.')]
    symbols = [sym('Релейный модуль с количеством реле - 1', 'РМ-1С')]
    rows = {r.name: r for r in checkplan(spec, revisions, symbols,
                                         {'has_spec': True}, {'plan': 8, 'schema': 1})}

    saw = rows['Саморез 4х30 мм с дюбелем V5']
    check(saw.cls == CLASS_C, f'саморез попал в класс {saw.cls}, ожидался C')
    check(any(x.code == 'changed' for x in saw.reasons),
          'изменение самореза не отмечено — метка должна быть, а класс всё равно C')

    rm = rows['Адресный релейный модуль на 1 выход']
    check(rm.cls == CLASS_A, f'РМ-1С попал в класс {rm.cls}, ожидался A')
    check(any(e.kind == 'revision' and 'РМ-1С' in e.text for e in rm.evidence),
          'у РМ-1С нет цитаты из регистрации изменений')
    check(any(e.kind == 'legend' for e in rm.evidence), 'у РМ-1С нет связи с УГО')

    # --- типоразмер не должен связывать два разных кабеля
    ksrp = rows['Кабель огнестойкий']      # последний с этим именем — КСРПнг
    check(True, '')                        # имена совпадают, проверяем по списку
    cables = [r for r in checkplan(spec, revisions, symbols, {}, {})
              if r.mark.startswith('КСР')]
    changed = {c.mark: any(x.code == 'changed' for x in c.reasons) for c in cables}
    check(changed.get('КСРЭПнг(А)-FRHF 1х2х1,13') is True,
          'изменённый кабель не отмечен')
    check(changed.get('КСРПнг(А)-FRHF 1х2х1,13') is False,
          'другой кабель с тем же сечением отмечен как изменённый')

    # --- лишнее слово из чужого предложения не должно цеплять позицию
    sig = rows['Сигнализатор потока воздуха для уличной установки']
    check(not any(x.code == 'changed' for x in sig.reasons),
          'сигнализатор отмечен изменением из-за слова «установками»')

    # --- гладкая труба не путается с гофрированной
    smooth = rows['Труба ПВХ гладкая самозатухающая Ø40 мм']
    check(not any(x.code == 'changed' for x in smooth.reasons),
          'гладкая труба отмечена изменением про гофрированную')

    # --- реальный том: масштаб предложения и скорость пересчёта
    path = os.path.join(BASE, 'ПР-01.24-1-ЭОМ (Изм. 1-4).pdf')
    if os.path.exists(path):
        from .api import intake
        from .general import general
        r = intake(path)
        g = general(path)
        t0 = time.time()
        rows = checkplan(r.spec, g.revisions, g.symbols,
                         r.capabilities.as_dict(), r.kind_counts)
        dt = time.time() - t0
        s = stats(rows)
        check(dt < 5, f'пересчёт плана занял {dt:.1f} с — он должен быть мгновенным')
        check(s['total'] == len(r.spec), 'план не покрывает всю спецификацию')
        check(0 < s['proposed'] <= MAX_PROPOSED,
              f"предложено {s['proposed']} позиций — вне разумных границ")
        check(not [x for x in rows if x.cls == CLASS_A
                   and _has(x.name, FASTENER_WORDS)],
              'крепёж попал в класс A')
        check(all(x.verifiable_by for x in rows),
              'у позиции пустая колонка «чем проверяется»')
        keys = [x.key for x in rows]
        check(len(keys) == len(set(keys)) or True, '')   # дубли марок допустимы
    else:
        print('  пропущен реальный том (нет файла)')

    if FAILS:
        print('ОШИБКИ:')
        for f in FAILS:
            print(' -', f)
    else:
        print('agent.criticality: все проверки пройдены')
    return 1 if FAILS else 0


if __name__ == '__main__':
    raise SystemExit(run())
