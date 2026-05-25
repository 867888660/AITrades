from flask import Blueprint, request, jsonify
from ...services.template_service import save_template, get_templates, delete_template

template_bp = Blueprint('template', __name__)

@template_bp.route('/', methods=['GET'])
def list_templates():
    template_type = request.args.get('type')
    return jsonify(get_templates(template_type))

@template_bp.route('/', methods=['POST'])
def create_template():
    data = request.json
    t_id = save_template(
        data['name'],
        data['template_type'],
        data['config'],
        data.get('description', '')
    )
    return jsonify({"id": t_id, "message": "Template saved"})

@template_bp.route('/<int:template_id>', methods=['DELETE'])
def remove_template(template_id):
    delete_template(template_id)
    return jsonify({"message": "Template deleted"})
