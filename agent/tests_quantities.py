# -*- coding: utf-8 -*-
"""Проверки распознавания количеств и марок.

Запуск:  python -m agent.tests_quantities
"""
from .extract import _strip_stamp_fragments, expand_range, _flag_assemblies, _fix_docref_marks
from .match import (canon_mark, caption_qty, mark_core, is_searchable,
                    _caption_sum, longer_keys, norm_text)

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


# --- количественные подписи на чертежах
check(caption_qty(' (10 шт.)') == 10, 'скобочная подпись «(10 шт.)»')
check(caption_qty(' (трехфазный ввод) (50 шт.)') == 50, 'подпись после пояснения')
check(caption_qty(' - 3 шт.') == 3, 'подпись через дефис')
check(caption_qty(' - 2 м') is None, 'длина не является количеством')

# --- подпись заменяет счёт вхождений
vocab = {'уэрм-2.5': {'variants': {'уэрм-2.5'}, 'members': [{'уэрм-2.5'}], 'items': []},
         'уэрм-2.4': {'variants': {'уэрм-2.4'}, 'members': [{'уэрм-2.4'}], 'items': []},
         'уэрм-2.4 вариант 2': {'variants': {'уэрм-2.4 вариант 2'},
                                'members': [{'уэрм-2.4 вариант 2'}], 'items': []}}
lg = longer_keys(vocab)
lines = [norm_text('УЭРМ-2.5 (10 шт.)')]
check(_caption_sum(lines, {'уэрм-2.5'}, [], lg) == 10, 'количество берётся из подписи')
lines = [norm_text('УЭРМ-2.4 вариант 2 (1 шт.)')]
check(_caption_sum(lines, {'уэрм-2.4'}, [], lg) == 0,
      'подпись более длинной марки не приписывается базовой')
check(_caption_sum(lines, {'уэрм-2.4 вариант 2'}, [], lg) == 1, 'подпись длинной марки')

# --- диапазоны обозначений
check(expand_range('ЩМ08..ЩМ12') == ['ЩМ08', 'ЩМ09', 'ЩМ10', 'ЩМ11', 'ЩМ12'],
      'раскрытие диапазона ЩМ08..ЩМ12')
check(expand_range('ЩМк') == ['ЩМк'], 'одиночное обозначение')
check(expand_range('') == [], 'пустое примечание')

# --- марка-ссылка на лист -> обозначение из примечания
items = [{'pos': '1.4', 'name': 'Щиток механизации ... в составе:',
          'mark': 'ПР-01/24-1-ЭОМ лист 14', 'note': 'ЩМк'},
         {'pos': '1.4.1', 'name': 'Корпус металлический',
          'mark': 'ЩРн-12 IP31 EKF PROxima', 'note': ''},
         {'pos': '1.5', 'name': 'Щит механизации ... в составе:',
          'mark': 'ПР-01/24-1-ЭОМ лист 15', 'note': 'ЩМ08..ЩМ12'}]
_fix_docref_marks(items)
check(items[0]['mark'] == 'ЩМк', 'марка из примечания вместо ссылки на лист')
check(items[2]['mark_variants'] == ['ЩМ08', 'ЩМ09', 'ЩМ10', 'ЩМ11', 'ЩМ12'],
      'варианты из диапазона в примечании')
_flag_assemblies(items)
check(items[0].get('composite'), 'составное изделие помечено')
check(items[1].get('component_of') == '1.4', 'комплектующее привязано к щиту')

# --- нормализация марки
check(mark_core(canon_mark('ЩРн-12 IP31 EKF PROxima')) == 'щрн-12',
      'ядро марки без вендора и IP')
check(canon_mark('УЭРМ-1.4 на 2 этаже') == 'уэрм-1.4', 'уточнение места отброшено')
check(is_searchable('щмк', relaxed=True), 'короткое обозначение из примечания ищется')
check(not is_searchable('щмк'), 'короткая марка без цифр по умолчанию не ищется')

# --- обрывки текста печати в ячейках
check(_strip_stamp_fragments('<< РОИ ТШП-0,66-60 400/5 кл.т. 0,5S ОО')
      == 'ТШП-0,66-60 400/5 кл.т. 0,5S', 'обрывки печати срезаны')
check(_strip_stamp_fragments('АБО Acti9 iC60L 3P 6A SE') == 'Acti9 iC60L 3P 6A SE',
      'обрывок печати перед латинской маркой')
check(_strip_stamp_fragments('ВА-335Е-3Р-400А') == 'ВА-335Е-3Р-400А',
      'нормальная марка не изменена')

if FAILS:
    print('ОШИБКИ:')
    for f in FAILS:
        print('  -', f)
    raise SystemExit(1)
print('все проверки пройдены')
