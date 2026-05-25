from flask import Blueprint, render_template

page_bp = Blueprint('page', __name__)

@page_bp.route('/')
def dashboard():
    return render_template('dashboard.html')

@page_bp.route('/settings')
def settings():
    return render_template('settings.html')
