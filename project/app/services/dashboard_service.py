import json
from ..models.system_models import Dashboard, DashboardWidget, DashboardLayout, ChartConfig
from ..extensions import db

def get_dashboards():
    dashboards = Dashboard.query.all()
    return [{"id": d.id, "name": d.name, "description": d.description} for d in dashboards]

def create_dashboard(name, description=""):
    dashboard = Dashboard(name=name, description=description)
    db.session.add(dashboard)
    db.session.commit()
    
    # Initialize empty layout
    layout = DashboardLayout(dashboard_id=dashboard.id, layout_json="[]")
    db.session.add(layout)
    db.session.commit()
    
    return {"id": dashboard.id, "name": dashboard.name}

def get_dashboard_layout(dashboard_id):
    layout = DashboardLayout.query.filter_by(dashboard_id=dashboard_id).first()
    if not layout:
        return []
    return json.loads(layout.layout_json)

def save_dashboard_layout(dashboard_id, layout_json):
    layout = DashboardLayout.query.filter_by(dashboard_id=dashboard_id).first()
    if not layout:
        layout = DashboardLayout(dashboard_id=dashboard_id, layout_json=json.dumps(layout_json))
        db.session.add(layout)
    else:
        layout.layout_json = json.dumps(layout_json)
    db.session.commit()
    return True

def add_widget(dashboard_id, chart_id, widget_key, x=0, y=0, w=12, h=8):
    widget = DashboardWidget(dashboard_id=dashboard_id, chart_id=chart_id, widget_key=widget_key)
    db.session.add(widget)
    
    # Update layout implicitly
    layout_str = get_dashboard_layout(dashboard_id)
    if isinstance(layout_str, str):
        layout = json.loads(layout_str)
    else:
        layout = layout_str
        
    layout.append({
        "id": widget_key,
        "x": x,
        "y": y,
        "w": w,
        "h": h
    })
    save_dashboard_layout(dashboard_id, layout)
    db.session.commit()
    return True

def remove_widget(dashboard_id, widget_key):
    DashboardWidget.query.filter_by(dashboard_id=dashboard_id, widget_key=widget_key).delete()
    
    layout = get_dashboard_layout(dashboard_id)
    layout = [w for w in layout if w.get('id') != widget_key]
    save_dashboard_layout(dashboard_id, layout)
    
    db.session.commit()
    return True
