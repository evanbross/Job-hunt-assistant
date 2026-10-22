# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-sonnet-5 | 2026-10-18
"""app.py's generated Swagger/OpenAPI docs (_build_swagger_template et al.) --
previously untested, and previously showed every request body as a generic
optional object regardless of what the route actually validated. Now reads
the real Pydantic model each route's `.model_validate(...)` call uses, where
one exists, and falls back to the old generic placeholder otherwise."""

from __future__ import annotations

import app as app_module
from routes.contacts import ContactEmploymentHistoryUpdate


def test_apispec_endpoint_serves_the_generated_spec() -> None:
    """The real boundary this feature has to work through: flasgger renders
    /apispec_1.json from the template at request time, and would reject a
    malformed spec (duplicate keys, a $ref that cannot be inlined, etc.)."""
    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()

    response = client.get("/apispec_1.json")

    assert response.status_code == 200
    data = response.get_json()
    assert data["paths"]
    assert data["definitions"]


def test_apidocs_ui_page_still_renders() -> None:
    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()

    response = client.get("/apidocs/")

    assert response.status_code == 200


def test_every_dollar_ref_resolves_to_a_real_definition() -> None:
    spec = app_module._build_swagger_template(app_module.app)
    definitions = spec["definitions"]

    for path, methods in spec["paths"].items():
        for method, operation in methods.items():
            for parameter in operation.get("parameters", []):
                schema = parameter.get("schema") or {}
                if "$ref" in schema:
                    ref_name = schema["$ref"].rsplit("/", 1)[-1]
                    assert ref_name in definitions, f"{path} {method}: missing definition {ref_name!r}"


def test_a_route_with_a_pydantic_body_model_shows_its_real_schema() -> None:
    spec = app_module._build_swagger_template(app_module.app)
    operation = spec["paths"]["/api/contact-employment-history/{entry_id}"]["patch"]
    body_param = next(p for p in operation["parameters"] if p["name"] == "body")

    assert body_param["schema"] == {"$ref": "#/definitions/ContactEmploymentHistoryUpdate"}
    definition = spec["definitions"]["ContactEmploymentHistoryUpdate"]
    assert set(definition["properties"]) == set(ContactEmploymentHistoryUpdate.model_fields)


def test_a_route_with_no_model_validate_call_falls_back_to_a_generic_body() -> None:
    """routes/import_.py::import_and_queue_all_jobs is deliberately left
    hand-parsed (see TODO.md's 2026-09-29 entry) -- its payload shape isn't
    fully enumerable as a strict model, so there's no real schema to show."""
    spec = app_module._build_swagger_template(app_module.app)
    operation = spec["paths"]["/api/import/queue-all"]["post"]
    body_param = next(p for p in operation["parameters"] if p["name"] == "body")

    assert body_param["schema"] == {"type": "object"}


def test_a_route_with_a_docstring_surfaces_it_as_the_description() -> None:
    spec = app_module._build_swagger_template(app_module.app)
    operation = spec["paths"]["/api/account/audit-log"]["get"]

    assert "description" in operation
    assert operation["summary"] != "Get Audit Log"  # not the generic title-cased fallback


def test_a_route_with_no_docstring_still_falls_back_to_a_titled_summary() -> None:
    spec = app_module._build_swagger_template(app_module.app)
    operation = spec["paths"]["/api/contact-employment-history/{entry_id}"]["delete"]

    assert "description" not in operation
    assert operation["summary"]
