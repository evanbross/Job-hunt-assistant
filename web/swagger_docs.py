# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Generated Swagger/OpenAPI docs for the /api/* routes (UI at /apidocs/, JSON at
/apispec_1.json). Moved out of app.py unchanged on 2026-11-05.

Building the template only introspects `app.url_map` and each view's own source, so
it does no I/O and is safe to run inside `create_app()`.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Callable

import flask
import flask.json as flask_json
from flask import Flask
from markupsafe import Markup
from pydantic import BaseModel

# Flasgger still reaches for flask.Markup and flask.json.JSONEncoder, both removed in
# Flask 3; these shims have to exist before flasgger is imported below.
if not hasattr(flask, "Markup"):
    flask.Markup = Markup
if not hasattr(flask_json, "JSONEncoder"):
    flask_json.JSONEncoder = json.JSONEncoder

from flasgger import Swagger  # noqa: E402 -- must follow the shims above

_FLASK_PATH_PARAM_RE = re.compile(
    r"<(?:(?P<converter>[a-zA-Z_][a-zA-Z0-9_]*):)?(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)>"
)
_SWAGGER_EXCLUDED_METHODS = frozenset({"HEAD", "OPTIONS"})
_SWAGGER_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


def _swagger_parameter_type(converter: str | None) -> tuple[str, str | None]:
    if converter == "int":
        return "integer", "int32"
    if converter == "float":
        return "number", "float"
    return "string", None


def _swagger_path_for_rule(rule_path: str) -> tuple[str, list[dict[str, object]]]:
    parameters: list[dict[str, object]] = []

    def _replace_path_param(path_match: re.Match[str]) -> str:
        param_name = path_match.group("name")
        param_type, param_format = _swagger_parameter_type(
            path_match.group("converter")
        )
        parameter: dict[str, object] = {
            "name": param_name,
            "in": "path",
            "required": True,
            "type": param_type,
        }
        if param_format:
            parameter["format"] = param_format
        parameters.append(parameter)
        return f"{{{param_name}}}"

    return _FLASK_PATH_PARAM_RE.sub(_replace_path_param, rule_path), parameters


def _swagger_tag_for_rule(rule_path: str) -> str:
    segments = [segment for segment in rule_path.split("/") if segment]
    if len(segments) < 2:
        return "API"
    return segments[1].replace("-", " ").title()


# ai: claude-sonnet-5 | 2026-10-18
# Matches this codebase's consistent `<Model>.model_validate(request.get_json(...))`
# pattern (see TODO.md's 2026-09-29 Pydantic validation entry) so the generated docs
# can show the real request schema instead of a generic placeholder object.
_MODEL_VALIDATE_RE = re.compile(r"\b([A-Z]\w*)\.model_validate\(")


def _extract_request_model(view_func: Callable[..., object]) -> type[BaseModel] | None:
    """Best-effort: the Pydantic model a route handler validates its request body
    against, found by scanning the handler's own source for a `.model_validate(`
    call and resolving that name through the handler's own module globals --
    the same globals Python itself would use to resolve that name at call time,
    so this never guesses at a mapping that isn't already true in the code.
    Returns None for a route with no such call, or if source isn't available
    (e.g. a dynamically built function) -- the caller falls back to the
    previous generic placeholder schema."""
    try:
        source = inspect.getsource(view_func)
    except (OSError, TypeError):
        return None
    match = _MODEL_VALIDATE_RE.search(source)
    if not match:
        return None
    candidate = view_func.__globals__.get(match.group(1))
    if isinstance(candidate, type) and issubclass(candidate, BaseModel):
        return candidate
    return None


def _swagger_body_schema(
    view_func: Callable[..., object] | None, definitions: dict[str, object]
) -> dict[str, object]:
    model = _extract_request_model(view_func) if view_func is not None else None
    if model is None:
        return {"type": "object"}
    # model_json_schema() can return shared sub-schemas under "$defs" for a
    # model with nested Pydantic fields -- fold those into the same top-level
    # `definitions` dict (Swagger 2.0's name for it) that ref_template points at,
    # rather than leaving them stranded under the top model's own schema.
    schema = model.model_json_schema(ref_template="#/definitions/{model}")
    nested_definitions = schema.pop("$defs", None)
    if nested_definitions:
        definitions.update(nested_definitions)
    definitions[model.__name__] = schema
    return {"$ref": f"#/definitions/{model.__name__}"}


def _swagger_operation_for_rule(
    endpoint: str,
    method: str,
    rule_path: str,
    path_parameters: list[dict[str, object]],
    view_func: Callable[..., object] | None,
    definitions: dict[str, object],
) -> dict[str, object]:
    parameters = list(path_parameters)
    if method in _SWAGGER_BODY_METHODS:
        parameters.append(
            {
                "name": "body",
                "in": "body",
                "required": False,
                "schema": _swagger_body_schema(view_func, definitions),
            }
        )

    # ai: claude-sonnet-5 | 2026-10-18 — a real per-route description (when the
    # handler has a docstring) instead of just the title-cased endpoint name;
    # still falls back to that for the ~70% of routes with no docstring.
    docstring = inspect.getdoc(view_func) if view_func is not None else None
    summary = docstring.splitlines()[0].strip() if docstring else endpoint.replace("_", " ").title()
    operation: dict[str, object] = {
        "tags": [_swagger_tag_for_rule(rule_path)],
        "summary": summary,
        "operationId": f"{endpoint}_{method.lower()}",
        "responses": {
            "200": {"description": "Successful response"},
            "400": {"description": "Invalid request"},
            "500": {"description": "Server error"},
        },
    }
    if docstring:
        operation["description"] = docstring
    if parameters:
        operation["parameters"] = parameters
    return operation


def build_swagger_template(flask_app: Flask) -> dict[str, object]:
    """The OpenAPI 2.0 document for every /api/* rule currently on `flask_app`."""
    paths: dict[str, dict[str, object]] = {}
    definitions: dict[str, object] = {}
    for url_rule in sorted(
        flask_app.url_map.iter_rules(), key=lambda route_rule: route_rule.rule
    ):
        if not url_rule.rule.startswith("/api/"):
            continue

        view_func = flask_app.view_functions.get(url_rule.endpoint)
        swagger_path, path_parameters = _swagger_path_for_rule(url_rule.rule)
        path_item = paths.setdefault(swagger_path, {})
        for method in sorted(url_rule.methods - _SWAGGER_EXCLUDED_METHODS):
            path_item[method.lower()] = _swagger_operation_for_rule(
                endpoint=url_rule.endpoint,
                method=method,
                rule_path=url_rule.rule,
                path_parameters=path_parameters,
                view_func=view_func,
                definitions=definitions,
            )

    return {
        "swagger": "2.0",
        "info": {
            "title": "Job Hunt Assistant API",
            # ai: claude-sonnet-5 | 2026-10-18 — request bodies now show the real
            # Pydantic schema where one could be resolved (see
            # _extract_request_model); responses are still a generic 200/400/500,
            # since no route defines a response model this doc generator could
            # read the same way.
            "description": (
                "Auto-generated route inventory for the Job Hunt Assistant Flask "
                "backend. Request-body schemas are read directly from the route's "
                "own Pydantic model where one is used (most POST/PUT/PATCH routes); "
                "a route with no such call falls back to a generic optional object. "
                "Every response is still shown as a generic 200/400/500 — read the "
                "route handler for actual response shapes and status codes."
            ),
            "version": "0.1.0",
        },
        "basePath": "/",
        "schemes": ["http", "https"],
        "consumes": ["application/json"],
        "produces": ["application/json"],
        "paths": paths,
        "definitions": definitions,
    }


# ai: claude-opus-5-5 | 2026-11-05
def install_swagger(app: Flask) -> None:
    """Serve generated docs for `app`. Call after every blueprint is registered, since
    the template is built from the routes present at this moment."""
    app.config["SWAGGER"] = {
        "title": "Job Hunt Assistant API",
        "uiversion": 3,
    }
    Swagger(app, template=build_swagger_template(app))
