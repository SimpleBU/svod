# -*- coding: utf-8 -*-
"""Воркер очереди: python -m portal.worker"""
import logging

from . import config
from .db import upgrade_schema


def main():
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    if config.RUN_MIGRATIONS:
        upgrade_schema()
    if not config.REDIS_URL:
        raise SystemExit('REDIS_URL не задан: воркеру нечего слушать')
    from redis import Redis
    from rq import Queue, Worker
    conn = Redis.from_url(config.REDIS_URL)
    Worker([Queue(config.QUEUE_NAME, connection=conn)], connection=conn).work(
        with_scheduler=False)


if __name__ == '__main__':
    main()
