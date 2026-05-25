import os
import threading
import time

from ..models.system_models import DbRegistry
from .datasource_service import datasource_manager, normalize_db_path
from .schema_service import scan_database_schema


class DatasourceWatchService:
    def __init__(self):
        self._thread = None
        self._stop_event = threading.Event()
        self._known_mtimes = {}
        self._lock = threading.Lock()

    def start(self, app):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return

            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._watch_loop,
                args=(app,),
                name='datasource-watch-service',
                daemon=True
            )
            self._thread.start()

    def _watch_loop(self, app):
        interval = max(float(app.config.get('DATASOURCE_WATCH_INTERVAL_SECONDS', 5)), 1.0)
        while not self._stop_event.wait(interval):
            with app.app_context():
                self._poll_datasources()

    def _poll_datasources(self):
        active_ids = set()
        for db_record in DbRegistry.query.all():
            db_id = db_record.id
            file_path = normalize_db_path(db_record.file_path)
            active_ids.add(db_id)

            if not file_path or not os.path.exists(file_path):
                datasource_manager.remove_engine(db_id)
                self._known_mtimes.pop(db_id, None)
                continue

            current_mtime = os.path.getmtime(file_path)
            previous_mtime = self._known_mtimes.get(db_id)

            if previous_mtime is None:
                self._known_mtimes[db_id] = current_mtime
                if not db_record.last_scanned_at:
                    self._rescan_datasource(db_id)
                continue

            if current_mtime > previous_mtime:
                self._known_mtimes[db_id] = current_mtime
                self._rescan_datasource(db_id)

        for db_id in list(self._known_mtimes.keys()):
            if db_id not in active_ids:
                self._known_mtimes.pop(db_id, None)
                datasource_manager.remove_engine(db_id)

    def _rescan_datasource(self, db_id):
        try:
            datasource_manager.remove_engine(db_id)
            scan_database_schema(db_id)
        except Exception as exc:
            print(f'[datasource-watch-service] Failed to refresh db {db_id}: {exc}')


datasource_watch_service = DatasourceWatchService()
