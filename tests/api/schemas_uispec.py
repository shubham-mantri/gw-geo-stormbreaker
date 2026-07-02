"""ui-spec §6 JSON schemas -- the single source of truth the ``web/`` types + the backend response
models both mirror (M2-T21).

``test_contract_fidelity.py`` validates every M2 read/settings endpoint's *actual* response against
the matching schema here, so a drift between the backend, this contract, and ``web/lib/types.ts``
fails loudly rather than silently reaching the dashboard. Each schema is ``additionalProperties:
false`` -- a field the backend emits but the ui-spec does not list is a contract breach, not a
harmless extra -- and lists exactly the fields ui-spec §6 / §3.1-§3.8 specify (the response models
in ``gw_geo.api.schemas`` are the binding server-side mirror).

These are hand-written JSON Schema (draft 2020-12) dicts rather than generated from the Pydantic
models on purpose: generating them from the same models they are meant to police would make the test
tautological. The ui-spec is the authority; these transcribe it.
"""

from __future__ import annotations

from typing import Any

# A confidence interval is serialized from ``tuple[float, float]`` as a two-element ``[low, high]``.
_CI_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {"type": "number"},
    "minItems": 2,
    "maxItems": 2,
}

# avg_position is nullable (ui-spec §3.2: "—" when an engine returns no ranked position).
_NULLABLE_NUMBER: dict[str, Any] = {"type": ["number", "null"]}
_NULLABLE_STRING: dict[str, Any] = {"type": ["string", "null"]}


# GET /brands -> [{id,name,domain,competitors[]}]
BRANDS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "name", "domain", "competitors"],
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "domain": {"type": "string"},
            "competitors": {"type": "array", "items": {"type": "string"}},
        },
    },
}


# GET /brands/{id}/overview -> {sov,mention_rate,pipeline,leads,trend[]}
OVERVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sov", "mention_rate", "pipeline", "leads", "trend"],
    "properties": {
        "sov": {"type": "number"},
        "mention_rate": {"type": "number"},
        "pipeline": {"type": "number"},
        "leads": {"type": "integer"},
        "trend": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["date", "you", "competitor"],
                "properties": {
                    "date": {"type": "string"},
                    "you": {"type": "number"},
                    "competitor": {"type": "number"},
                },
            },
        },
    },
}


# GET /brands/{id}/visibility -> {engines:[{engine,mention_rate,ci,cited,avg_position,sentiment,
#                                          n_samples,trend[]}], prompts:[{...}]}
_VISIBILITY_ENGINE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "engine",
        "mention_rate",
        "ci",
        "cited",
        "avg_position",
        "sentiment",
        "n_samples",
        "trend",
    ],
    "properties": {
        "engine": {"type": "string"},
        "mention_rate": {"type": "number"},
        "ci": _CI_SCHEMA,
        "cited": {"type": "number"},
        "avg_position": _NULLABLE_NUMBER,
        "sentiment": {"type": "number"},
        "n_samples": {"type": "integer"},
        "trend": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["date", "mention_rate"],
                "properties": {
                    "date": {"type": "string"},
                    "mention_rate": {"type": "number"},
                },
            },
        },
    },
}

_VISIBILITY_PROMPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["prompt_id", "text", "mention_rate", "avg_position", "n_samples"],
    "properties": {
        "prompt_id": {"type": "string"},
        "text": {"type": "string"},
        "mention_rate": {"type": "number"},
        "avg_position": _NULLABLE_NUMBER,
        "n_samples": {"type": "integer"},
    },
}

VISIBILITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["engines", "prompts"],
    "properties": {
        "engines": {"type": "array", "items": _VISIBILITY_ENGINE_SCHEMA},
        "prompts": {"type": "array", "items": _VISIBILITY_PROMPT_SCHEMA},
    },
}


# GET /brands/{id}/sources -> [{domain,source_type,you_pct,competitor_pcts}]
SOURCES_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["domain", "source_type", "you_pct", "competitor_pcts"],
        "properties": {
            "domain": {"type": "string"},
            "source_type": {"type": "string"},
            "you_pct": {"type": "number"},
            "competitor_pcts": {
                "type": "object",
                "additionalProperties": {"type": "number"},
            },
        },
    },
}


# GET /brands/{id}/pipeline -> {influenced,attributed,leads,lift,top_answers[],method_breakdown,
#                               confidence_note}
PIPELINE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "influenced",
        "attributed",
        "leads",
        "lift",
        "top_answers",
        "method_breakdown",
        "confidence_note",
    ],
    "properties": {
        "influenced": {"type": "number"},
        "attributed": {"type": "number"},
        "leads": {"type": "integer"},
        "lift": {"type": "number"},
        "top_answers": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["prompt", "leads", "value"],
                "properties": {
                    "prompt": {"type": "string"},
                    "leads": {"type": "integer"},
                    "value": {"type": "number"},
                },
            },
        },
        "method_breakdown": {
            "type": "object",
            "additionalProperties": False,
            "required": ["direct", "citation_linked", "assisted", "holdout_incremental"],
            "properties": {
                "direct": {"type": "number"},
                "citation_linked": {"type": "number"},
                "assisted": {"type": "number"},
                "holdout_incremental": {"type": "number"},
            },
        },
        # The anti-overclaim disclosure is part of the contract and is never empty (PRD §13).
        "confidence_note": {"type": "string", "minLength": 1},
    },
}


# GET /brands/{id}/alerts -> [{severity,message,ts}]
ALERTS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["severity", "message", "ts"],
        "properties": {
            "severity": {"enum": ["red", "green", "yellow"]},
            "message": {"type": "string"},
            "ts": {"type": "string"},
        },
    },
}


# GET /brands/{id}/prompts -> [{id,text,intent_cluster,geo,persona}]
PROMPTS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "text", "intent_cluster", "geo", "persona"],
        "properties": {
            "id": {"type": "string"},
            "text": {"type": "string"},
            "intent_cluster": _NULLABLE_STRING,
            "geo": {"type": "string"},
            "persona": _NULLABLE_STRING,
        },
    },
}


# GET /lead-capture/snippet -> {snippet}
SNIPPET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["snippet"],
    "properties": {"snippet": {"type": "string", "minLength": 1}},
}
