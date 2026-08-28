# -*- coding: utf-8 -*-
"""Разбор имени файла: шифр тома, раздел, ревизия.

Бюро называет файл шифром тома — это единственный источник раздела,
пока файл не разобран. Разбор терпимый: если не распознали, показываем
имя файла как есть и не выдумываем.
"""
import re

# Разделы. Дописывать сюда по мере встречи новых шифров — это дешевле,
# чем ветвления в коде.
SECTION_LABELS = {
    'ЭОМ': 'Электрооборудование',
    'ЭМ': 'Силовое электрооборудование',
    'ЭО': 'Электроосвещение',
    'ЭС': 'Электроснабжение',
    'ЭН': 'Наружное электроснабжение',
    'ОВ': 'Отопление и вентиляция',
    'ОВК': 'Отопление, вентиляция, кондиционирование',
    'ТМ': 'Тепломеханические решения',
    'ВК': 'Водоснабжение и канализация',
    'НВК': 'Наружные сети водоснабжения',
    'АСУД': 'Автоматизация и диспетчеризация',
    'АК': 'Автоматизация',
    'СС': 'Слаботочные системы',
    'АУПС': 'Пожарная сигнализация',
    'АР': 'Архитектурные решения',
    'КР': 'Конструктивные решения',
    'ПРК': 'ПРК',
}

REV_PAT = re.compile(
    r'[\s_(\[]+((?:с\s*)?изм[\.\s№]*[\d\s,;и\-–\.]*\d)\s*[)\]]?\s*$', re.I)
CIPHER_PAT = re.compile(r'(ПР[-\s_][\w\.\-_/]*?-([А-ЯЁ]{2,5}\d*))(?=[\s_(\[\.]|$)')


def _norm_rev(rev):
    rev = re.sub(r'\s+', ' ', rev).strip(' .,_')
    rev = re.sub(r'^с\s*', '', rev, flags=re.I)
    rev = re.sub(r'^(изм)[\.\s]*', r'\1. ', rev, flags=re.I)
    rev = re.sub(r'\s*,\s*', ', ', rev)
    return rev[:1].upper() + rev[1:] if rev else ''


def parse_filename(filename):
    """-> (cipher, section, section_label, revision).

    'ПР-01.24-1-ЭОМ (Изм. 1-4).pdf' -> ('ПР-01.24-1-ЭОМ', 'ЭОМ',
                                        'Электрооборудование', 'Изм. 1-4')
    """
    stem = re.sub(r'\.pdf$', '', (filename or '').strip(), flags=re.I)
    m = REV_PAT.search(stem)
    revision = _norm_rev(m.group(1)) if m else ''
    head = stem[:m.start()] if m else stem
    head = head.strip(' _-([')

    cm = CIPHER_PAT.search(head)
    if cm:
        cipher, section = cm.group(1).strip(), cm.group(2).upper()
    else:
        cipher = head
        tail = re.split(r'[-_\s]', head)[-1] if head else ''
        section = tail.upper() if re.fullmatch(r'[А-ЯЁA-Z]{2,5}\d*', tail or '') else ''

    label = ''
    if section:
        label = SECTION_LABELS.get(section, '')
        if not label:
            base = re.sub(r'\d+$', '', section)
            label = SECTION_LABELS.get(base, section)
    return cipher, section, label, revision
