import os
from flask import Flask
from .config import Config
from .extensions import db, cors

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    watcher_state = {'started': False}

    # Initialize Flask extensions here
    db.init_app(app)
    cors.init_app(app)

    # Register blueprints here
    from .blueprints.page.routes import page_bp
    from .blueprints.datasource.routes import datasource_bp
    from .blueprints.schema.routes import schema_bp
    from .blueprints.chart.routes import chart_bp
    from .blueprints.dashboard.routes import dashboard_bp
    from .blueprints.template.routes import template_bp

    app.register_blueprint(page_bp)
    app.register_blueprint(datasource_bp, url_prefix='/api/datasources')
    app.register_blueprint(schema_bp, url_prefix='/api/schema')
    app.register_blueprint(chart_bp, url_prefix='/api/chart')
    app.register_blueprint(dashboard_bp, url_prefix='/api/dashboards')
    app.register_blueprint(template_bp, url_prefix='/api/templates')

    with app.app_context():
        # Import models so they are registered with SQLAlchemy
        from .models import system_models
        
        # Ensure instance dir exists
        os.makedirs(app.config['INSTANCE_DIR'], exist_ok=True)
        
        # Create system tables if they don't exist
        db.create_all()

        # Add panes_config_json if it doesn't exist (migration)
        try:
            db.session.execute(db.text('ALTER TABLE chart_config ADD COLUMN panes_config_json TEXT'))
            db.session.commit()
        except Exception:
            db.session.rollback()

    @app.before_request
    def ensure_datasource_watcher_started():
        if watcher_state['started'] or not app.config.get('AUTO_WATCH_DATASOURCES', True):
            return

        from .services.datasource_watch_service import datasource_watch_service
        datasource_watch_service.start(app)
        watcher_state['started'] = True

    return app
