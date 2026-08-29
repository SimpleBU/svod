# -*- coding: utf-8 -*-
"""Очередь разбора.

В Redis только id задач: веб кладёт номер тома, воркер его забирает.
Прогресс и результат идут в Postgres. Без REDIS_URL задача выполняется
в отдельном потоке того же процесса — это режим ноутбука, не продакшена.
"""
import logging
import threading

from . import config

log = logging.getLogger(__name__)


def _redis_queue():
    from redis import Redis
    from rq import Queue
    return Queue(config.QUEUE_NAME, connection=Redis.from_url(config.REDIS_URL),
                 default_timeout=int(config.UPLOAD_TTL) * 2)


def enqueue_intake(document_id):
    if config.REDIS_URL and not config.INLINE_WORKER:
        job = _redis_queue().enqueue('portal.tasks.run_intake', document_id,
                                     job_timeout=7200, result_ttl=3600)
        log.info('том %s поставлен в очередь (%s)', document_id, job.id)
        return job.id
    from .tasks import run_intake
    t = threading.Thread(target=run_intake, args=(document_id,), daemon=True,
                         name=f'intake-{document_id}')
    t.start()
    log.info('том %s разбирается в потоке (локальный режим)', document_id)
    return None


def enqueue_render(document_id):
    """Отрисовка листов тома для просмотрщика. Дешевле сверки, но всё равно
    минуты на комплект, поэтому в фоне."""
    if config.REDIS_URL and not config.INLINE_WORKER:
        job = _redis_queue().enqueue('portal.tasks.run_render', document_id,
                                     job_timeout=3600, result_ttl=600)
        log.info('отрисовка тома %s поставлена в очередь (%s)', document_id, job.id)
        return job.id
    from .tasks import run_render
    t = threading.Thread(target=run_render, args=(document_id,), daemon=True,
                         name=f'render-{document_id}')
    t.start()
    return None


def enqueue_match(document_id):
    """Сверка с чертежами: та же очередь, отдельная задача.

    Сверка дороже приёмки — она читает все планы и схемы, — поэтому
    запускается руками с вкладки, а не сама после разбора.
    """
    if config.REDIS_URL and not config.INLINE_WORKER:
        job = _redis_queue().enqueue('portal.tasks.run_match', document_id,
                                     job_timeout=7200, result_ttl=3600)
        log.info('сверка тома %s поставлена в очередь (%s)', document_id, job.id)
        return job.id
    from .tasks import run_match
    t = threading.Thread(target=run_match, args=(document_id,), daemon=True,
                         name=f'match-{document_id}')
    t.start()
    log.info('том %s сверяется в потоке (локальный режим)', document_id)
    return None
