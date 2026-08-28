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
