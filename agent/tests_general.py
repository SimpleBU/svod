# -*- coding: utf-8 -*-
"""Проверки разбора листа «Общие данные» на реальных томах из Example/.

Запуск:  python -m agent.tests_general

Числа в эталонах сняты с реальных томов и сверены глазами по PDF. Если
проверка упала — сначала откройте лист общих данных этого тома, а потом
правьте разбор: расхождение здесь означает либо регресс, либо вёрстку,
которой раньше не встречалось.
"""
import os

from .general import general, REFERENCED, ATTACHED
from .symbols import render

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'Example')

# том -> (листов в ведомости, пропуски, ссылочных, прилагаемых,
#         комплектов раздела, УГО, записей об изменениях)
GOLDEN = {
    'ПР-01.24-1-ЭОМ (Изм. 1-4).pdf':   (59, [49], 6, 3, 9, 0, 4),
    'ПР-01.24-3-ПРК с изм.1,2.pdf':    (21, [],   5, 0, 0, 20, 8),
    'ПР-0124-1-ОВ1_изм.1,2.pdf':       (24, [],   2, 4, 6, 0, 32),
    'ПР-01_24-АСУД (Изм.1).pdf':       (85, [],   7, 3, 0, 0, 1),
}

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


def run():
    for name, gold in GOLDEN.items():
        path = os.path.join(BASE, name)
        if not os.path.exists(path):
            print(f'  пропущен (нет файла): {name}')
            continue
        r = general(path)
        got = (len(r.sheets), r.gaps(),
               sum(1 for d in r.refs if d.kind == REFERENCED),
               sum(1 for d in r.refs if d.kind == ATTACHED),
               len(r.volumes), len(r.symbols), len(r.revisions))
        check(got == gold, f'{name}: получено {got}, ожидалось {gold}')
        check(not r.warnings, f'{name}: предупреждения {r.warnings}')

    # объявленное число листов ссылочного документа читается из примечания
    path = os.path.join(BASE, 'ПР-01.24-1-ЭОМ (Изм. 1-4).pdf')
    if os.path.exists(path):
        r = general(path)
        kj = next((d for d in r.refs if d.code.endswith('.КЖ')), None)
        so = next((d for d in r.refs if d.code.endswith('.СО')), None)
        check(kj is not None and kj.sheets_declared == 45,
              'кабельный журнал объявлен на 45 листах')
        check(so is not None and so.sheets_declared == 30,
              'спецификация объявлена на 30 листах')

    # картинки условных обозначений: в ПРК легенда систем, двадцать строк,
    # у каждой цветная линия — значит графика в ячейке есть у всех
    path = os.path.join(BASE, 'ПР-01.24-3-ПРК с изм.1,2.pdf')
    if os.path.exists(path):
        g = general(path)
        imgs = render(path, g.symbols)
        check(len(imgs) == 20, f'ПРК: картинок УГО {len(imgs)}, ожидалось 20')
        blank = [i.name for i in imgs if i.blank]
        check(not blank, f'ПРК: пустые ячейки УГО {blank[:3]}')
        big = [i.name for i in imgs if i.png and len(i.png) > 100_000]
        check(not big, f'ПРК: картинка УГО больше 100 КБ {big[:3]}')
        check(all(i.width <= 340 for i in imgs if i.png),
              'ПРК: картинка УГО шире ограничения')

    if FAILS:
        print('ОШИБКИ:')
        for f in FAILS:
            print(' -', f)
    else:
        print('agent.general: все проверки пройдены')
    return 1 if FAILS else 0


if __name__ == '__main__':
    raise SystemExit(run())
