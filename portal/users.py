# -*- coding: utf-8 -*-
"""Заведение и правка пользователей из командной строки.

    python -m portal.users list
    python -m portal.users add expert@bureau.ru "Иванов И."
    python -m portal.users password expert@bureau.ru
    python -m portal.users disable expert@bureau.ru

Пароль спрашивается интерактивно и не остаётся ни в истории команд,
ни в репозитории.
"""
import getpass
import sys

from sqlalchemy import select

from . import auth
from .db import SessionLocal
from .models import User


def _ask_password():
    first = getpass.getpass('Пароль: ')
    if first != getpass.getpass('Ещё раз: '):
        raise SystemExit('пароли не совпали')
    if len(first) < auth.MIN_PASSWORD:
        raise SystemExit(f'пароль короче {auth.MIN_PASSWORD} символов')
    return first


def main(argv=None):
    argv = list(argv or sys.argv[1:])
    cmd = argv[0] if argv else 'list'
    with SessionLocal() as s:
        if cmd == 'list':
            users = s.scalars(select(User).order_by(User.id)).all()
            if not users:
                print('пользователей нет')
            for u in users:
                state = '' if u.is_active else '  (отключён)'
                last = u.last_login_at.strftime('%d.%m.%Y %H:%M') if u.last_login_at else 'ни разу'
                print(f'{u.id:>3}  {u.email:<34} {u.role:<8} вход: {last}{state}')
            return 0

        if cmd == 'add':
            if len(argv) < 2:
                raise SystemExit('нужна почта: python -m portal.users add почта [имя]')
            email, name = argv[1], (argv[2] if len(argv) > 2 else '')
            user = auth.create_user(s, email, _ask_password(), name=name)
            print(f'заведён {user.email}')
            return 0

        email = argv[1] if len(argv) > 1 else ''
        user = auth.find_user(s, email)
        if user is None:
            raise SystemExit(f'нет пользователя {email}')

        if cmd == 'password':
            auth.set_password(s, user, _ask_password())
            print(f'пароль {user.email} изменён')
        elif cmd in ('disable', 'enable'):
            user.is_active = cmd == 'enable'
            s.commit()
            print(f'{user.email}: {"включён" if user.is_active else "отключён"}')
        else:
            raise SystemExit(f'неизвестная команда {cmd}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
