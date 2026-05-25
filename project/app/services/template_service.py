import json
from ..models.system_models import ViewTemplate
from ..extensions import db

def save_template(name, template_type, config_dict, description=""):
    template = ViewTemplate(
        name=name,
        description=description,
        template_type=template_type,
        config_json=json.dumps(config_dict)
    )
    db.session.add(template)
    db.session.commit()
    return template.id

def get_templates(template_type=None):
    query = ViewTemplate.query
    if template_type:
        query = query.filter_by(template_type=template_type)
    templates = query.all()
    
    return [{
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "type": t.template_type,
        "config": json.loads(t.config_json)
    } for t in templates]

def delete_template(template_id):
    ViewTemplate.query.filter_by(id=template_id).delete()
    db.session.commit()
    return True
