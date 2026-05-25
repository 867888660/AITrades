from sqlalchemy import text
from ..models.system_models import DbRegistry, DbSchemaCache
from .datasource_service import datasource_manager

def build_and_execute_query(query_request):
    main_db_id = query_request.get('db_id')
    main_table_name = query_request.get('table_name')
    time_column = query_request.get('time_column')
    series = query_request.get('series', [])
    filters = query_request.get('filters', [])
    order = query_request.get('order', 'asc').lower()
    limit = query_request.get('limit', 5000)
    downsample_config = query_request.get('downsample', {})
    
    valid_ops = {'=', '!=', '>', '<', '>=', '<=', 'IN', 'LIKE'}
    
    series_data_results = []
    
    for s in series:
        s_db_id = s.get('db_id') or main_db_id
        s_table_name = s.get('table_name') or main_table_name
        s_col = s['column']
        
        # 1. Validate Table and Columns (Whitelist)
        cache_entries = DbSchemaCache.query.filter_by(db_id=s_db_id, table_name=s_table_name).all()
        if not cache_entries:
            raise ValueError(f"Table {s_table_name} not found or not scanned in db {s_db_id}")
            
        valid_columns = {c.column_name for c in cache_entries}
        
        if time_column and time_column not in valid_columns:
            # If a series is from another table and doesn't have the main time_column, we might have an issue.
            # We assume the user ensures the time_column exists or we ignore time_column for that series.
            pass
            
        if s_col not in valid_columns:
            raise ValueError(f"Series column {s_col} not found in {s_table_name}")
                
        for f in filters:
            if f['column'] not in valid_columns:
                # Skip filters that don't apply to this table
                continue
        
        # 2. Build Query Statement
        if time_column and time_column in valid_columns:
            select_clause = f'"{time_column}", "{s_col}"'
            order_str = "ASC" if order == "asc" else "DESC"
            order_clause = f'ORDER BY "{time_column}" {order_str}'
        else:
            select_clause = f'"{s_col}"'
            order_clause = ""
        
        where_clauses = []
        params = {}
        for i, f in enumerate(filters):
            col = f['column']
            if col not in valid_columns:
                continue
            op = str(f['op']).upper()
            if op not in valid_ops:
                continue
            val = f['value']
            param_name = f"p_{i}"
            
            if op == 'IN':
                if not isinstance(val, list):
                    val = [val]
                placeholders = ", ".join([f":{param_name}_{j}" for j in range(len(val))])
                where_clauses.append(f'"{col}" IN ({placeholders})')
                for j, v in enumerate(val):
                    params[f"{param_name}_{j}"] = v
            else:
                where_clauses.append(f'"{col}" {op} :{param_name}')
                if op == 'LIKE':
                    val = f"%{val}%"
                params[param_name] = val
                
        where_str = " AND ".join(where_clauses) if where_clauses else "1=1"
        
        sql = f'SELECT {select_clause} FROM "{s_table_name}" WHERE {where_str} {order_clause} LIMIT :limit'
        params['limit'] = limit
        
        # 3. Execute Query
        engine = datasource_manager.get_engine(s_db_id)
        with engine.connect() as conn:
            result = conn.execute(text(sql), params)
            rows = result.fetchall()
            
        # 4. Process Rows (Downsampling)
        returned_rows = len(rows)
        if downsample_config.get('enabled') and downsample_config.get('max_points'):
            max_points = downsample_config['max_points']
            if returned_rows > max_points:
                step = returned_rows / max_points
                sampled_rows = []
                for i in range(max_points):
                    idx = int(i * step)
                    if idx < returned_rows:
                        sampled_rows.append(rows[idx])
                rows = sampled_rows
                
        # Format output: [[time, val], [time, val]] or [[val], [val]]
        formatted_rows = [list(r) for r in rows]
        series_data_results.append(formatted_rows)
        
    return {
        "multi_series": True,
        "columns": {
            "time": time_column,
            "series": [s['column'] for s in series]
        },
        "series_data": series_data_results,
        "meta": {
            "multi_db": True
        }
    }
