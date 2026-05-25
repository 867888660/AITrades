from datetime import datetime
from ..extensions import db

class DbRegistry(db.Model):
    __tablename__ = 'db_registry'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), nullable=False)
    db_type = db.Column(db.String(20), nullable=False, default='sqlite')
    role = db.Column(db.String(50), default='general')
    file_path = db.Column(db.String(500), nullable=False, unique=True)
    is_active = db.Column(db.Integer, nullable=False, default=1)
    description = db.Column(db.Text)
    created_at = db.Column(db.String(30), nullable=False, default=lambda: datetime.utcnow().isoformat())
    updated_at = db.Column(db.String(30), nullable=False, default=lambda: datetime.utcnow().isoformat())
    last_scanned_at = db.Column(db.String(30))

class DbSchemaCache(db.Model):
    __tablename__ = 'db_schema_cache'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    db_id = db.Column(db.Integer, db.ForeignKey('db_registry.id'), nullable=False)
    table_name = db.Column(db.String(100), nullable=False)
    column_name = db.Column(db.String(100), nullable=False)
    data_type = db.Column(db.String(50))
    is_nullable = db.Column(db.Integer)
    is_time_candidate = db.Column(db.Integer, default=0)
    is_numeric_candidate = db.Column(db.Integer, default=0)
    ordinal_position = db.Column(db.Integer)
    scanned_at = db.Column(db.String(30), nullable=False, default=lambda: datetime.utcnow().isoformat())
    
    # 关系
    db_registry = db.relationship('DbRegistry', backref=db.backref('schema_cache', cascade="all, delete-orphan"))

class Dashboard(db.Model):
    __tablename__ = 'dashboard'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    created_at = db.Column(db.String(30), nullable=False, default=lambda: datetime.utcnow().isoformat())
    updated_at = db.Column(db.String(30), nullable=False, default=lambda: datetime.utcnow().isoformat())

class ChartConfig(db.Model):
    __tablename__ = 'chart_config'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), nullable=False)
    db_id = db.Column(db.Integer, db.ForeignKey('db_registry.id'), nullable=False)
    table_name = db.Column(db.String(100), nullable=False)
    time_column = db.Column(db.String(100), nullable=False)
    query_config_json = db.Column(db.Text, nullable=False)
    series_config_json = db.Column(db.Text, nullable=False)
    panes_config_json = db.Column(db.Text)
    y_axis_config_json = db.Column(db.Text)
    style_config_json = db.Column(db.Text)
    created_at = db.Column(db.String(30), nullable=False, default=lambda: datetime.utcnow().isoformat())
    updated_at = db.Column(db.String(30), nullable=False, default=lambda: datetime.utcnow().isoformat())

class DashboardWidget(db.Model):
    __tablename__ = 'dashboard_widget'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    dashboard_id = db.Column(db.Integer, db.ForeignKey('dashboard.id'), nullable=False)
    chart_id = db.Column(db.Integer, db.ForeignKey('chart_config.id'), nullable=False)
    widget_key = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.String(30), nullable=False, default=lambda: datetime.utcnow().isoformat())

class DashboardLayout(db.Model):
    __tablename__ = 'dashboard_layout'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    dashboard_id = db.Column(db.Integer, db.ForeignKey('dashboard.id'), nullable=False, unique=True)
    layout_json = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.String(30), nullable=False, default=lambda: datetime.utcnow().isoformat())

class ViewTemplate(db.Model):
    __tablename__ = 'view_template'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    template_type = db.Column(db.String(50), nullable=False) # 'chart', 'dashboard', 'query'
    config_json = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.String(30), nullable=False, default=lambda: datetime.utcnow().isoformat())
    updated_at = db.Column(db.String(30), nullable=False, default=lambda: datetime.utcnow().isoformat())
