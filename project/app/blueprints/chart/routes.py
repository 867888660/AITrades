import json
from flask import Blueprint, request, jsonify
from ...models.system_models import ChartConfig
from ...extensions import db
from ...services.chart_service import build_and_execute_query

chart_bp = Blueprint('chart', __name__)

@chart_bp.route('/query', methods=['POST'])
def query_chart_data():
    try:
        data = request.json
        result = build_and_execute_query(data)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@chart_bp.route('/', methods=['POST'])
def save_chart_config():
    data = request.json
    chart = ChartConfig(
        name=data['name'],
        db_id=data['db_id'],
        table_name=data['table_name'],
        time_column=data['time_column'],
        query_config_json=json.dumps(data.get('query', {})),
        series_config_json=json.dumps(data.get('series', [])),
        panes_config_json=json.dumps(data.get('panes', [])),
        y_axis_config_json=json.dumps(data.get('y_axis', {})),
        style_config_json=json.dumps(data.get('style', {}))
    )
    db.session.add(chart)
    db.session.commit()
    return jsonify({"id": chart.id, "message": "Chart config saved"})

@chart_bp.route('/<int:chart_id>', methods=['GET'])
def get_chart_config(chart_id):
    chart = ChartConfig.query.get_or_404(chart_id)
    
    panes = []
    if chart.panes_config_json:
        panes = json.loads(chart.panes_config_json)
        
    return jsonify({
        "id": chart.id,
        "name": chart.name,
        "db_id": chart.db_id,
        "table_name": chart.table_name,
        "time_column": chart.time_column,
        "query": json.loads(chart.query_config_json),
        "series": json.loads(chart.series_config_json),
        "panes": panes,
        "y_axis": json.loads(chart.y_axis_config_json) if chart.y_axis_config_json else {},
        "style": json.loads(chart.style_config_json) if chart.style_config_json else {}
    })

@chart_bp.route('/<int:chart_id>', methods=['PUT'])
def update_chart_config(chart_id):
    chart = ChartConfig.query.get_or_404(chart_id)
    data = request.json
    if 'name' in data:
        chart.name = data['name']
    if 'db_id' in data:
        chart.db_id = data['db_id']
    if 'table_name' in data:
        chart.table_name = data['table_name']
    if 'time_column' in data:
        chart.time_column = data['time_column']
    if 'series' in data:
        chart.series_config_json = json.dumps(data['series'])
    if 'panes' in data:
        chart.panes_config_json = json.dumps(data['panes'])
    if 'y_axis' in data:
        chart.y_axis_config_json = json.dumps(data['y_axis'])
    if 'style' in data:
        chart.style_config_json = json.dumps(data['style'])
    if 'query' in data:
        chart.query_config_json = json.dumps(data['query'])
        
    db.session.commit()
    return jsonify({"id": chart.id, "message": "Chart config updated"})