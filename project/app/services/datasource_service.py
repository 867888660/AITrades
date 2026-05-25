import os
from sqlalchemy import create_engine
from ..models.system_models import DbRegistry
from ..extensions import db

SQLITE_FILE_EXTENSIONS = {'.db', '.sqlite', '.sqlite3'}

def normalize_db_path(file_path):
    if not file_path:
        return ''
    cleaned = str(file_path).strip().strip('"').strip("'")
    return os.path.abspath(os.path.normpath(cleaned))

def is_supported_sqlite_file(file_path):
    _, ext = os.path.splitext(file_path or '')
    return ext.lower() in SQLITE_FILE_EXTENSIONS

def discover_sqlite_files(folder_path, recursive=True):
    normalized_folder = normalize_db_path(folder_path)
    if not normalized_folder or not os.path.isdir(normalized_folder):
        raise NotADirectoryError(f"Folder not found: {normalized_folder or folder_path}")

    matches = []
    if recursive:
        for root, _, files in os.walk(normalized_folder):
            for file_name in files:
                if is_supported_sqlite_file(file_name):
                    matches.append(normalize_db_path(os.path.join(root, file_name)))
    else:
        for file_name in os.listdir(normalized_folder):
            full_path = os.path.join(normalized_folder, file_name)
            if os.path.isfile(full_path) and is_supported_sqlite_file(file_name):
                matches.append(normalize_db_path(full_path))

    return sorted(set(matches), key=lambda path: path.lower())

class DatasourceManager:
    _instance = None
    _engines = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DatasourceManager, cls).__new__(cls)
        return cls._instance

    def get_engine(self, db_id, db_path=None):
        if db_id not in self._engines:
            if not db_path:
                db_record = DbRegistry.query.get(db_id)
                if not db_record:
                    raise ValueError(f"Database with id {db_id} not found")
                db_path = db_record.file_path

            db_path = normalize_db_path(db_path)
                
            if not os.path.exists(db_path):
                raise FileNotFoundError(f"Database file not found at {db_path}")
                
            uri = f"sqlite:///{db_path}"
            self._engines[db_id] = create_engine(uri, future=True)
            
        return self._engines[db_id]

    def remove_engine(self, db_id):
        engine = self._engines.pop(db_id, None)
        if engine:
            engine.dispose()

    def test_connection(self, db_path):
        db_path = normalize_db_path(db_path)
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"Database file not found at {db_path}")
        try:
            uri = f"sqlite:///{db_path}"
            engine = create_engine(uri, future=True)
            with engine.connect() as conn:
                pass
            engine.dispose()
            return True
        except Exception as e:
            raise Exception(f"Failed to connect to database: {str(e)}")

# Create a singleton instance
datasource_manager = DatasourceManager()
