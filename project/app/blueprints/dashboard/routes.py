from flask import Blueprint, request, jsonify
from ...services.dashboard_service import (
    get_dashboards, create_dashboard, get_dashboard_layout,
    save_dashboard_layout, add_widget, remove_widget
)

dashboard_bp = Blueprint('dashboard', __name__)

@dashboard_bp.route('/', methods=['GET'])
def list_dashboards():
    return jsonify(get_dashboards())

@dashboard_bp.route('/', methods=['POST'])
def new_dashboard():
    data = request.json
    return jsonify(create_dashboard(data['name'], data.get('description', '')))

@dashboard_bp.route('/<int:dashboard_id>/layout', methods=['GET'])
def get_layout(dashboard_id):
    return jsonify(get_dashboard_layout(dashboard_id))

@dashboard_bp.route('/<int:dashboard_id>/layout', methods=['PUT'])
def save_layout(dashboard_id):
    layout_json = request.json
    save_dashboard_layout(dashboard_id, layout_json)
    return jsonify({"message": "Layout saved"})

@dashboard_bp.route('/<int:dashboard_id>/widgets', methods=['POST'])
def add_dashboard_widget(dashboard_id):
    data = request.json
    add_widget(
        dashboard_id, 
        data['chart_id'], 
        data['widget_key'],
        x=data.get('x', 0),
        y=data.get('y', 0),
        w=data.get('w', 12),
        h=data.get('h', 8)
    )
    return jsonify({"message": "Widget added"})

@dashboard_bp.route('/<int:dashboard_id>/widgets/<widget_key>', methods=['DELETE'])
def remove_dashboard_widget(dashboard_id, widget_key):
    remove_widget(dashboard_id, widget_key)
    return jsonify({"message": "Widget removed"})
