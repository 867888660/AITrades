from flask import Blueprint, request, jsonify
from ...models.system_models import DbRegistry
from ...extensions import db
from ...services.schema_service import scan_database_schema
from ...services.datasource_service import (
    datasource_manager,
    discover_sqlite_files,
    normalize_db_path,
)

datasource_bp = Blueprint('datasource', __name__)

@datasource_bp.route('/', methods=['GET'])
def get_datasources():
    dbs = DbRegistry.query.all()
    return jsonify([{
        "id": d.id,
        "name": d.name,
        "db_type": d.db_type,
        "role": d.role,
        "file_path": d.file_path,
        "is_active": d.is_active,
        "last_scanned_at": d.last_scanned_at,
        "updated_at": d.updated_at
    } for d in dbs])

@datasource_bp.route('/', methods=['POST'])
def add_datasource():
    data = request.json or {}
    name = (data.get('name') or '').strip()
    file_path = normalize_db_path(data.get('file_path', ''))
    if not name:
        return jsonify({"error": "name is required"}), 400
    if not file_path:
        return jsonify({"error": "file_path is required"}), 400
    if not datasource_manager.test_connection(file_path):
        return jsonify({"error": "Unable to connect to database"}), 400

    existing = DbRegistry.query.filter_by(file_path=file_path).first()
    if existing:
        return jsonify({
            "error": f"Datasource already registered: {existing.name}",
            "id": existing.id
        }), 409

    db_record = DbRegistry(
        name=name,
        db_type=data.get('db_type', 'sqlite'),
        role=data.get('role', 'general'),
        file_path=file_path,
        description=data.get('description', '')
    )
    db.session.add(db_record)
    db.session.commit()
    return jsonify({"id": db_record.id, "message": "Datasource added successfully"})

@datasource_bp.route('/<int:db_id>', methods=['DELETE'])
def delete_datasource(db_id):
    datasource_manager.remove_engine(db_id)
    DbRegistry.query.filter_by(id=db_id).delete()
    db.session.commit()
    return jsonify({"message": "Datasource deleted"})

@datasource_bp.route('/scan-folder', methods=['POST'])
def scan_datasource_folder():
    data = request.json or {}
    folder_path = data.get('folder_path', '')
    role = data.get('role', 'general')
    recursive = bool(data.get('recursive', True))
    auto_scan = bool(data.get('auto_scan', True))

    try:
        file_paths = discover_sqlite_files(folder_path, recursive=recursive)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    existing_records = {
        normalize_db_path(record.file_path): record
        for record in DbRegistry.query.all()
    }

    created_records = []
    skipped_paths = []
    for file_path in file_paths:
        if file_path in existing_records:
            skipped_paths.append(file_path)
            continue

        db_record = DbRegistry(
            name=data.get('default_name') or file_path.rsplit('\\', 1)[-1].rsplit('/', 1)[-1].rsplit('.', 1)[0],
            db_type='sqlite',
            role=role,
            file_path=file_path,
            description=f"Auto imported from folder scan: {normalize_db_path(folder_path)}"
        )
        db.session.add(db_record)
        created_records.append(db_record)

    db.session.commit()

    scan_results = []
    scan_errors = []
    if auto_scan:
        for db_record in created_records:
            try:
                scan_results.append(scan_database_schema(db_record.id))
            except Exception as e:
                scan_errors.append({
                    "id": db_record.id,
                    "name": db_record.name,
                    "file_path": db_record.file_path,
                    "error": str(e)
                })

    return jsonify({
        "folder_path": normalize_db_path(folder_path),
        "discovered": len(file_paths),
        "imported": len(created_records),
        "skipped": len(skipped_paths),
        "scanned": len(scan_results),
        "scan_errors": scan_errors,
        "datasources": [{
            "id": record.id,
            "name": record.name,
            "file_path": record.file_path
        } for record in created_records]
    })

@datasource_bp.route('/<int:db_id>/scan', methods=['POST'])
def scan_datasource(db_id):
    try:
        result = scan_database_schema(db_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 400
