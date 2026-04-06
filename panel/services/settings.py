from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError

from panel.extensions import db
from panel.models import SystemSetting


CSS_FRAMEWORKS = {
    "bootstrap": {
        "label": "Bootstrap 5",
        "description": "Domyslny styl panelu oparty o Bootstrap 5.",
        "stylesheets": [],
    },
    "tailwind": {
        "label": "Tailwind CSS",
        "description": "Utility-first look z bardziej nowoczesna typografia i kontrastem.",
        "stylesheets": [
            "https://cdnjs.cloudflare.com/ajax/libs/tailwindcss/2.2.19/tailwind.min.css",
        ],
    },
    "bulma": {
        "label": "Bulma",
        "description": "Lekki, nowoczesny styl z wyrazniejszym spacingiem i kartami.",
        "stylesheets": [
            "https://cdn.jsdelivr.net/npm/bulma@1.0.2/css/bulma.min.css",
        ],
    },
    "foundation": {
        "label": "Foundation",
        "description": "Bardziej techniczny motyw z neutralna paleta i mocnymi obramowaniami.",
        "stylesheets": [
            "https://cdn.jsdelivr.net/npm/foundation-sites@6.8.1/dist/css/foundation.min.css",
        ],
    },
    "materialize": {
        "label": "Materialize CSS",
        "description": "Material Design z mocniejszymi cieniami i bardziej nasyconymi akcentami.",
        "stylesheets": [
            "https://cdnjs.cloudflare.com/ajax/libs/materialize/1.0.0/css/materialize.min.css",
        ],
    },
    "uikit": {
        "label": "UIKit",
        "description": "Czysty, systemowy styl z lagodnymi kontrastami i lekkimi powierzchniami.",
        "stylesheets": [
            "https://cdn.jsdelivr.net/npm/uikit@3.21.16/dist/css/uikit.min.css",
        ],
    },
    "skeleton": {
        "label": "Skeleton",
        "description": "Minimalny, jasny motyw z lekkim layoutem i prostymi komponentami.",
        "stylesheets": [
            "https://cdnjs.cloudflare.com/ajax/libs/skeleton/2.0.4/skeleton.min.css",
        ],
    },
    "purecss": {
        "label": "Pure.css",
        "description": "Bardzo lekki styl z prostymi tabelami i formularzami.",
        "stylesheets": [
            "https://cdn.jsdelivr.net/npm/purecss@3.0.0/build/pure-min.css",
        ],
    },
    "milligram": {
        "label": "Milligram",
        "description": "Minimalistyczny framework z delikatnymi liniami i spokojna typografia.",
        "stylesheets": [
            "https://cdn.jsdelivr.net/npm/normalize.css@8.0.1/normalize.css",
            "https://cdn.jsdelivr.net/npm/milligram@1.4.1/dist/milligram.min.css",
        ],
    },
}

CSS_FRAMEWORK_SETTING_KEY = "ui.css_framework"


def css_framework_choices() -> list[tuple[str, str]]:
    return [(key, config["label"]) for key, config in CSS_FRAMEWORKS.items()]


def get_setting(key: str, default: str | None = None) -> str | None:
    try:
        setting = SystemSetting.query.filter_by(key=key).first()
    except SQLAlchemyError:
        return default
    if setting is None or not setting.value:
        return default
    return setting.value


def set_setting(key: str, value: str, description: str | None = None) -> SystemSetting:
    setting = SystemSetting.query.filter_by(key=key).first()
    if setting is None:
        setting = SystemSetting(key=key)
        db.session.add(setting)
    setting.value = value
    if description is not None:
        setting.description = description
    return setting


def get_css_framework_key() -> str:
    value = get_setting(CSS_FRAMEWORK_SETTING_KEY, "bootstrap") or "bootstrap"
    if value not in CSS_FRAMEWORKS:
        return "bootstrap"
    return value


def get_css_framework_config() -> dict[str, object]:
    key = get_css_framework_key()
    config = CSS_FRAMEWORKS[key]
    return {
        "key": key,
        "label": config["label"],
        "description": config["description"],
        "stylesheets": list(config["stylesheets"]),
    }
