# -*- coding: utf-8 -*-
"""Проверки фасада: паспорт тома и расхождения.

Запуск:  python -m agent.tests_passport
"""
import os
from types import SimpleNamespace as NS

from .api import _filename_revisions, _findings, RED, AMBER

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'Example')

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


def _gen(sheets=(), refs=(), symbols=(), revisions=()):
    return NS(sheets=[NS(no=n, title='', revisions=[], mark='', note_raw='', src_page=1)
                      for n in sheets],
              refs=list(refs), volumes=[], symbols=list(symbols),
              revisions=list(revisions), guidelines_text='', warnings=[])


def _res(spec=(), kind_counts=None, bad_pages=(), sheets=()):
    # sheets нужны проверке «объявлено — не сдано»: шифры со штампов листов
    # самого тома считаются сданными наравне с шифрами подачи
    return NS(spec=list(spec), sheets=list(sheets),
              kind_counts=kind_counts or {},
              capabilities=NS(unreadable_font_pages=list(bad_pages)))


def ref(code, title='', sheets=None, page=3, kind='attached'):
    return NS(code=code, title=title, sheets_declared=sheets, kind=kind,
              note_raw='', src_page=page)


def spec_row(mark, name='', unit='шт.', qty=1.0):
    return NS(mark=mark, name=name, canon=mark.lower(), unit=unit, qty=qty, pos='')


def sym(code, name='', page=1):
    return NS(code=code, name=name, page=page, bbox=(0, 0, 1, 1))


def run():
    # --- номера изменений берутся из блока «Изм. …», а не из всего имени
    check(_filename_revisions('ПР-01.24-1-ЭОМ (Изм. 1-4).pdf') == {1, 2, 3, 4},
          'диапазон изменений в имени файла разобран неверно')
    check(_filename_revisions('ПР-0124-1-ОВ1_изм.1,2.pdf') == {1, 2},
          'перечисление изменений в имени файла разобрано неверно')
    check(_filename_revisions('ПР-01.24-3-ПРК.pdf') == set(),
          'номера изменений придуманы из шифра тома')

    # --- пропуск в нумерации ведомости
    f = _findings(_gen(sheets=[1, 2, 3, 5]), _res(), [])
    check(any(x.code == 'sheet_gap' and x.level == RED and '4' in x.text for x in f),
          'пропуск листа в ведомости не найден')
    check(not [x for x in _findings(_gen(sheets=[1, 2, 3]), _res(), [])
               if x.code == 'sheet_gap'], 'пропуск найден там, где его нет')

    # --- объявленный документ отсутствует в подаче
    refs = [ref('ПР-01/24-1-ЭОМ.КЖ', 'Кабельный журнал', 45)]
    f = _findings(_gen(refs=refs), _res(), [], submission_codes=['ПР-01/24-1-ЭОМ'])
    check(any(x.code == 'ref_missing' and '45' in x.text for x in f),
          'отсутствующий кабельный журнал не отмечен')
    f = _findings(_gen(refs=refs), _res(), [],
                  submission_codes=['ПР-01/24-1-ЭОМ', 'ПР-01/24-1-ЭОМ.КЖ'])
    check(not [x for x in f if x.code == 'ref_missing'],
          'документ есть в подаче, но отмечен как отсутствующий')

    # приложение, подшитое в тот же файл, отсутствующим не считается
    f = _findings(_gen(refs=refs),
                  _res(sheets=[NS(code='ПР-01/24-1-ЭОМ.КЖ', page=40)]), [],
                  submission_codes=['ПР-01/24-1-ЭОМ'])
    check(not [x for x in f if x.code == 'ref_missing'],
          'кабельный журнал внутри того же тома отмечен как отсутствующий')

    # за ссылочные документы бюро не отвечает: типовая серия и ГОСТ объявлены
    # как основание решения, а не как то, что нужно сдать
    serie = [ref('Серия Б5.000-2.1', 'Крепление трубопроводов', kind='referenced')]
    f = _findings(_gen(refs=serie), _res(), [], submission_codes=['ПР-01/24-1-ОВ1'])
    check(not [x for x in f if x.code == 'ref_missing'],
          'типовая серия отмечена как несданный документ')

    # тот же документ под другим шифром — это не «файла нет»
    other = [ref('ПР-01/24-8.2-ОВ1.СО', 'Спецификация', 55)]
    f = _findings(_gen(refs=other),
                  _res(sheets=[NS(code='ПР-01/24-2-ОВ1.СО', page=60)]), [],
                  submission_codes=['ПР-01/24-2-ОВ1'])
    codes = [x.code for x in f]
    check('ref_cipher' in codes and 'ref_missing' not in codes,
          f'расхождение шифров подано как отсутствие файла: {codes}')
    cipher = next(x for x in f if x.code == 'ref_cipher')
    check('ПР-01/24-2-ОВ1.СО' in cipher.text,
          'в замечании о шифрах нет человеческого написания найденного шифра')

    # шифр с указанием листов — тот же документ
    refs2 = [ref('ПР-01/24-3-СПСиА.СО(л. 1-3)', 'Спецификация', 3)]
    f = _findings(_gen(refs=refs2),
                  _res(sheets=[NS(code='ПР-01/24-3-СПСиА.СО', page=20)]), [],
                  submission_codes=['ПР-01/24-3-СПСиА'])
    check(not [x for x in f if x.code == 'ref_missing'],
          'хвост «(л. 1-3)» помешал опознать сданный документ')

    # --- объявленное число листов спецификации против разобранного
    refs = [ref('ПР-01/24-1-ЭОМ.СО', 'Спецификация', 30)]
    f = _findings(_gen(refs=refs), _res(kind_counts={'spec': 28}), [])
    check(any(x.code == 'spec_sheets' and x.level == AMBER for x in f),
          'расхождение по числу листов спецификации не найдено')
    f = _findings(_gen(refs=refs), _res(kind_counts={'spec': 30}), [])
    check(not [x for x in f if x.code == 'spec_sheets'],
          'расхождение найдено там, где числа сошлись')

    # --- обозначения легенды: одна строка на все, и только у легенды оборудования
    equip = [sym('РМ-1С', 'Релейный модуль'), sym('АМ-1', 'Метка адресная'),
             sym('KLZ', 'Клапан дымоудаления'), sym('ШУВ', 'Шкаф вентилятора')]
    spec = [spec_row('РМ-1С-R3', 'Релейный модуль'), spec_row('АМ-1-R3', 'Метка')]
    f = [x for x in _findings(_gen(symbols=equip), _res(spec), []) if x.code == 'symbol_unused']
    check(len(f) == 1, f'ожидалась одна строка про обозначения, получено {len(f)}')
    check(f and 'KLZ' in f[0].text and 'ШУВ' in f[0].text and 'РМ-1С' not in f[0].text,
          'в строке про обозначения не те коды')

    systems = [sym('В1', 'водопровод'), sym('Т3.1', 'горячий подающий'),
               sym('К1н', 'канализация напорная'), sym('К14', 'дренаж')]
    f = [x for x in _findings(_gen(symbols=systems), _res(spec), []) if x.code == 'symbol_unused']
    check(not f, 'легенда обозначений систем принята за легенду оборудования')

    # --- реальный том: пропуск листа 49 в ЭОМ
    path = os.path.join(BASE, 'ПР-01.24-1-ЭОМ (Изм. 1-4).pdf')
    if os.path.exists(path):
        from .api import intake, passport, checkplan
        res = intake(path)
        psp = passport(path, res=res, filename=os.path.basename(path), with_images=False)
        gaps = [x for x in psp.findings if x.code == 'sheet_gap']
        check(gaps and '49' in gaps[0].text, 'в ЭОМ не найден пропуск листа 49')
        check(not [x for x in psp.findings if x.code == 'revision_mismatch'],
              'изменения 1-4 из имени файла не сошлись с регистрацией')
        check(len(psp.norms) > 10, 'нормативы тома не разобраны')
        rows = checkplan(res, psp)
        check(len(rows) == len(res.spec), 'план проверки не покрывает спецификацию')
    else:
        print('  пропущен реальный том (нет файла)')

    if FAILS:
        print('ОШИБКИ:')
        for f in FAILS:
            print(' -', f)
    else:
        print('agent.passport: все проверки пройдены')
    return 1 if FAILS else 0


if __name__ == '__main__':
    raise SystemExit(run())
