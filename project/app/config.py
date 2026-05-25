import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-key-for-trading-visualization'
    
    # 获取 project/instance 目录作为系统数据库存放位置
    BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
    INSTANCE_DIR = os.path.join(BASE_DIR, 'instance')
    
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.join(INSTANCE_DIR, 'system.db')}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    AUTO_WATCH_DATASOURCES = os.environ.get('AUTO_WATCH_DATASOURCES', '1') != '0'
    DATASOURCE_WATCH_INTERVAL_SECONDS = float(os.environ.get('DATASOURCE_WATCH_INTERVAL_SECONDS', '5'))