# Локальный прогон портала: SQLite, файлы на диске, разбор в потоке.
import os, pathlib
os.environ['DATABASE_URL'] = 'sqlite:///' + str(pathlib.Path('var/local.db').absolute())
os.environ['STORAGE_BACKEND'] = 'local'
os.environ['STORAGE_DIR'] = str(pathlib.Path('var/files').absolute())
os.environ['RUN_MIGRATIONS'] = '0'
os.environ['INLINE_WORKER'] = '1'
os.environ.pop('REDIS_URL', None)
pathlib.Path('var').mkdir(exist_ok=True)

from portal.db import engine
from portal.models import Base
Base.metadata.create_all(engine)
print('схема создана')

import uvicorn
from portal.web.app import app
uvicorn.run(app, host='127.0.0.1', port=8099, log_level='warning')
