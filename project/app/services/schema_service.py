from datetime import datetime
from sqlalchemy import inspect
from ..extensions import db
from ..models.system_models import DbRegistry, DbSchemaCache
from .datasource_service import datasource_manager

def scan_database_schema(db_id):
    db_record = DbRegistry.query.get(db_id)
    if not db_record:
        raise ValueError(f"Database with id {db_id} not found")
        
    engine = datasource_manager.get_engine(db_id, db_record.file_path)
    inspector = inspect(engine)
    
    # First, clear old cache for this db_id
    DbSchemaCache.query.filter_by(db_id=db_id).delete()
    
    tables = inspector.get_table_names()
    columns_count = 0
    now_iso = datetime.utcnow().isoformat()
    
    for table_name in tables:
        columns = inspector.get_columns(table_name)
        for i, col in enumerate(columns):
            col_name = col['name']
            col_type = str(col['type']).upper()
            
            # Identify time candidates
            is_time = 0
            if any(t in col_name.lower() for t in ['time', 'date', 'created', 'updated', 'saved']):
                is_time = 1
            if any(t in col_type for t in ['DATETIME', 'DATE', 'TIMESTAMP']):
                is_time = 1
            # Relax time candidate matching for SQLite TEXT columns that might hold dates
            if col_type == 'TEXT' and any(t in col_name.lower() for t in ['time', 'date', 'created', 'updated', 'saved']):
                is_time = 1
            
            # Since users might want any column as X-axis, let's treat all columns as potential X-axis 
            # for the dropdown, but keep is_time to prioritize them in the UI.
                
            # Identify numeric candidates
            is_numeric = 0
            if any(t in col_type for t in ['INT', 'REAL', 'FLOAT', 'NUMERIC', 'DECIMAL']):
                is_numeric = 1
            # Allow TEXT to be numeric candidate if the name suggests it's a value/price/amount
            if col_type == 'TEXT' and any(t in col_name.lower() for t in ['price', 'amount', 'value', 'count', 'sum', 'total', 'ask', 'bid']):
                is_numeric = 1
                
            cache_entry = DbSchemaCache(
                db_id=db_id,
                table_name=table_name,
                column_name=col_name,
                data_type=col_type,
                is_nullable=1 if col.get('nullable') else 0,
                is_time_candidate=is_time,
                is_numeric_candidate=is_numeric,
                ordinal_position=i + 1,
                scanned_at=now_iso
            )
            db.session.add(cache_entry)
            columns_count += 1
            
    # Update last scanned time
    db_record.last_scanned_at = now_iso
    db_record.updated_at = now_iso
    db.session.commit()
    
    return {
        "db_id": db_id,
        "tables": len(tables),
        "columns": columns_count,
        "scanned_at": now_iso
    }
