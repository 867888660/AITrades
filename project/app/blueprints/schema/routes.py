from flask import Blueprint, jsonify
from ...models.system_models import DbSchemaCache

schema_bp = Blueprint('schema', __name__)

@schema_bp.route('/<int:db_id>', methods=['GET'])
def get_schema(db_id):
    entries = DbSchemaCache.query.filter_by(db_id=db_id).all()
    
    schema = {}
    for entry in entries:
        if entry.table_name not in schema:
            schema[entry.table_name] = []
            
        schema[entry.table_name].append({
            "column_name": entry.column_name,
            "data_type": entry.data_type,
            "is_time_candidate": bool(entry.is_time_candidate),
            "is_numeric_candidate": bool(entry.is_numeric_candidate)
        })
        
    return jsonify(schema)
