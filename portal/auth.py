# -*- coding: utf-8 -*-
"""Вход в портал.

Пароли хранятся хешем PBKDF2-SHA256 со своей солью на каждого пользователя —
это стандартная библиотека, без лишних зависимостей и без самодельной
криптографии. Сессия — подписанная кука Starlette, в ней только идентификатор
пользователя: ничего, что имело бы смысл подделывать, там не лежит.

Первый пользователь заводится переменными окружения при первом старте
(`ADMIN_EMAIL`, `ADMIN_PASSWORD`), дальше — командой
`python -m portal.users`. Пароль в коде и в репозитории не появляется никогда.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select

from . import config
from .models import Org, User

log = logging.getLogger(__name__)

ALGO = 'pbkdf2_sha256'
ITERATIONS = 240_000
SALT_BYTES = 16

SESSION_KEY = 'uid'
# страницы, доступные без входа: сама форма входа, статика и проба живости
PUBLIC_PATHS = ('/login', '/logout', '/static/', '/healthz')

MIN_PASSWORD = 8


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
    return f'{ALGO}${iterations}${salt.hex()}${digest.hex()}'


def verify_password(password: str, stored: str) -> bool:
    """Сравнение постоянного времени: по скорости ответа пароль подбирать нельзя."""
    try:
        algo, iterations, salt_hex, digest_hex = (stored or '').split('$')
        if algo != ALGO:
            return False
        digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'),
                                     bytes.fromhex(salt_hex), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def _now():
    return datetime.now(timezone.utc)


def find_user(session, email: str):
    email = (email or '').strip().lower()
    if not email:
        return None
    return session.scalar(select(User).where(func.lower(User.email) == email))


def authenticate(session, email: str, password: str):
    """-> User или None. Причину отказа наружу не сообщаем.

    Разные тексты для «нет такой почты» и «неверный пароль» позволяют
    перебирать существующие адреса, поэтому ответ один на оба случая.
    """
    user = find_user(session, email)
    if user is None or not user.is_active:
        # считаем хеш и для несуществующего пользователя: иначе по времени
        # ответа видно, заведён такой адрес или нет
        hash_password(password or '', iterations=1000)
        return None
    if not verify_password(password or '', user.password_hash):
        return None
    user.last_login_at = _now()
    session.commit()
    return user


def create_user(session, email, password, name='', org_id=None, role='expert'):
    if len(password or '') < MIN_PASSWORD:
        raise ValueError(f'пароль короче {MIN_PASSWORD} символов')
    email = (email or '').strip().lower()
    if not email:
        raise ValueError('нужна почта')
    if find_user(session, email) is not None:
        raise ValueError('такой пользователь уже есть')
    if org_id is None:
        org = session.scalar(select(Org).order_by(Org.id).limit(1))
        org_id = org.id if org else None
    user = User(org_id=org_id, email=email, name=name.strip() or email,
                password_hash=hash_password(password), role=role)
    session.add(user)
    session.commit()
    return user


def set_password(session, user, password):
    if len(password or '') < MIN_PASSWORD:
        raise ValueError(f'пароль короче {MIN_PASSWORD} символов')
    user.password_hash = hash_password(password)
    session.commit()
    return user


def ensure_admin(session):
    """Первый пользователь из окружения — только если в базе никого нет.

    Пароль берётся из ADMIN_PASSWORD и в репозитории не появляется. Если
    переменных нет, портал не заводит никого и честно пишет об этом в лог:
    молча открытый портал хуже, чем портал, в который некому войти.
    """
    if session.scalar(select(func.count()).select_from(User)):
        return None
    email = os.getenv('ADMIN_EMAIL', '').strip()
    password = os.getenv('ADMIN_PASSWORD', '')
    if not email or not password:
        log.warning('пользователей нет и ADMIN_EMAIL/ADMIN_PASSWORD не заданы — '
                    'войти в портал будет некому; заведите первого через '
                    'python -m portal.users add')
        return None
    try:
        user = create_user(session, email, password, name=os.getenv('ADMIN_NAME', ''),
                           role='admin')
    except ValueError as exc:
        log.error('не удалось завести первого пользователя: %s', exc)
        return None
    log.info('заведён первый пользователь %s', user.email)
    return user


def is_public(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in PUBLIC_PATHS)


def secret_key() -> str:
    """Ключ подписи куки. На проде задаётся окружением и переживает перезапуск.

    Без него сессии живут до ближайшего рестарта — для ноутбука это нормально,
    для прода нет, поэтому в render.yaml ключ генерируется площадкой.
    """
    key = os.getenv('SECRET_KEY', '').strip()
    if key:
        return key
    log.warning('SECRET_KEY не задан — сессии не переживут перезапуск сервиса')
    return secrets.token_urlsafe(48)


def login(request, user):
    request.session[SESSION_KEY] = user.id


def logout(request):
    request.session.pop(SESSION_KEY, None)


def current_user(request, session):
    uid = request.session.get(SESSION_KEY)
    if not uid:
        return None
    user = session.get(User, uid)
    if user is None or not user.is_active:
        request.session.pop(SESSION_KEY, None)
        return None
    return user


SESSION_MAX_AGE = int(os.getenv('SESSION_HOURS', '12')) * 3600
