# -*- coding: utf-8 -*-
"""Проверки фасада сверки (этап 3).

Запуск:  python -m agent.tests_match

Логика уровней и источников проверяется на строках, собранных руками.
На реальном томе проверяется то, что подделать нельзя: строки сверки
связываются с позициями плана проверки, счёт не разъезжается со
спецификацией, а время и память остаются в границах воркера.
"""
import os
import resource
import time

from .api import MATCH_LEVELS, RED, AMBER, OK, match_level, _source

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'Example')

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


def run():
    # --- уровень строки берётся по началу статуса: уточнение в скобках
    # не должно превращать расхождение в «сошлось»
    check(match_level('расхождение') == RED, 'расхождение не красное')
    check(match_level('расхождение (по кабельному журналу)') == RED,
          'расхождение с уточнением потеряло уровень')
    check(match_level('нет на чертежах') == RED, 'отсутствие на чертежах не красное')
    check(match_level('ок') == OK, '«ок» не зелёное')
    check(match_level('ок (по измерению)') == OK, '«ок по измерению» не зелёное')
    check(match_level('ок (частично)') == AMBER, 'частичное совпадение не жёлтое')
    check(match_level('узел: кол-во на 1 узел') == AMBER, 'узел не жёлтый')
    check(match_level('есть на чертежах, метраж не подписан') == AMBER,
          'неподписанный метраж не жёлтый')

    # «ок (частично)» должно проверяться раньше «ок»: иначе частичное
    # совпадение сойдёт за полное
    check([p for p, _ in MATCH_LEVELS].index('ок (частично)')
          < len(MATCH_LEVELS), 'порядок уровней потерян')

    check(_source('ок (по кабельному журналу)') == 'по кабельному журналу',
          'источник не извлечён из статуса')
    check(_source('расхождение') == '', 'источник придуман там, где его нет')

    # --- реальный том
    path = os.path.join(BASE, 'ПР-01.24-1-ЭОМ (Изм. 1-4).pdf')
    if not os.path.exists(path):
        print('  пропущен реальный том (нет файла)')
    else:
        from .api import intake, passport, checkplan, reconcile
        res = intake(path)
        psp = passport(path, res=res, filename=os.path.basename(path), with_images=False)
        plan = checkplan(res, psp)
        keys = {r.key for r in plan if r.cls == 'A'}
        check(keys, 'план проверки не предложил ни одной позиции класса A')

        t0 = time.time()
        m = reconcile(path, keys=keys)
        dt = time.time() - t0
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

        check(m.rows, 'сверка не дала ни одной строки')
        check(dt < 600, f'сверка заняла {dt:.0f} с — воркер столько не ждёт')
        check(rss < 1600, f'пик памяти {rss:.0f} МБ — близко к лимиту воркера 2 ГБ')

        # каждая строка сверки должна знать, из каких позиций она сложилась:
        # иначе расхождение не привязать к плану проверки
        keyless = [r for r in m.rows if not r.keys]
        check(not keyless,
              f'{len(keyless)} строк сверки не связаны с позициями спецификации')

        # ключи строк сверки и ключи плана проверки считаются одной функцией,
        # значит отобранные экспертом позиции обязаны находиться
        marked = [r for r in m.rows if r.in_plan]
        check(marked, 'ни одна строка сверки не легла на план проверки')
        check(m.stats['in_plan'] == len(marked), 'счётчик отобранных не сошёлся')

        check(m.stats['rows'] == len(m.rows), 'счётчик строк не сошёлся')
        check(m.stats['problems'] + m.stats['doubts'] + m.stats['matched']
              == len(m.rows), 'уровни не покрывают все строки')
        check(all(r.kind in ('count', 'length') for r in m.rows),
              'у строки сверки неизвестный вид')
        check(all(r.status for r in m.rows), 'у строки сверки пустой статус')

        # штучные позиции сверяются по маркам, метровые — по ключу кабеля;
        # и те и другие должны быть в томе ЭОМ
        check(any(r.kind == 'length' for r in m.rows), 'метровых позиций не найдено')
        check(any(r.kind == 'count' for r in m.rows), 'штучных позиций не найдено')

        print(f'  ЭОМ: {len(m.rows)} строк, из них по плану проверки {len(marked)}, '
              f'проблемных {m.stats["problems"]}; {dt:.0f} с, пик {rss:.0f} МБ')

    if FAILS:
        print('ОШИБКИ:')
        for f in FAILS:
            print(' -', f)
    else:
        print('agent.match: все проверки пройдены')
    return 1 if FAILS else 0


if __name__ == '__main__':
    raise SystemExit(run())
