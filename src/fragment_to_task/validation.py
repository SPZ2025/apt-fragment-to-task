from __future__ import annotations

import difflib
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .errors import PipelineError
from .utils import sha256_text

CATEGORIES = (
    "Background",
    "Motivation",
    "Goal",
    "Method",
    "Experiment",
    "Result",
    "Conclusion",
    "Hypothesis",
)
ELIGIBILITIES = ("keep", "borderline", "exclude")
TASKABILITY_DIMENSIONS = (
    "problem_reconstructable",
    "answer_leakage_avoidable",
    "requires_reasoning",
    "self_contained_task_feasible",
    "persona_discrimination_potential",
)
GENERATION_CHECKS = (
    "self_contained",
    "source_agnostic",
    "literal_leakage",
    "paraphrase_leakage",
    "causal_leakage",
)
EXPECTED_CHECKS = {
    "self_contained": True,
    "source_agnostic": True,
    "literal_leakage": False,
    "paraphrase_leakage": False,
    "causal_leakage": False,
}
LEAKAGE_FAILURE_TYPES = {
    "literal_leakage",
    "paraphrase_leakage",
    "causal_leakage",
    "not_self_contained",
    "source_dependent",
    "trivial_task",
    "other",
}
SOURCE_REFERENCE_RE = re.compile(
    r"\b(?:this|the)\s+(?:passage|paper|excerpt|fragment|text|article)\b|"
    r"\baccording to (?:the|this)\b|\bthe author(?:s)?\b|"
    r"(?:这|本|该)(?:篇|段|份)?(?:文章|论文|文献|文本|文段|片段|材料)|"
    r"根据(?:这|本|该|上述|上文)(?:篇|段|份)?"
    r"(?:文章|论文|文献|文本|文段|片段|材料|内容)|"
    r"(?:原文|上文|下文|作者(?:们)?)",
    flags=re.IGNORECASE,
)
WORD_RE = re.compile(r"[A-Za-z]+(?:[-'][A-Za-z]+)*")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
FORBIDDEN_ANSWER_KEYS = {
    "reference_answer",
    "model_answer",
    "answer",
    "rubric",
    "essential_points",
    "characteristic_points",
    "acceptable_alternatives",
    "fatal_errors",
}

CATEGORY_RULES: dict[str, dict[str, set[str]]] = {
    "Background": {
        "fields": {"domain_context", "known_observations", "concepts_or_relations"},
        "operations": {"explain", "compare", "organize", "analyze", "synthesize", "abstract"},
    },
    "Motivation": {
        "fields": {"problem_or_tension", "observed_limitation", "decision_context"},
        "operations": {"analyze", "diagnose", "justify", "compare", "assess"},
    },
    "Goal": {
        "fields": {"research_context", "unresolved_problem", "constraints"},
        "operations": {"formulate_goal", "formulate_question", "define_target", "prioritize"},
    },
    "Method": {
        "fields": {"problem", "objective", "constraints", "available_information"},
        "operations": {"design", "derive", "diagnose", "propose", "justify", "redesign", "compare"},
    },
    "Experiment": {
        "fields": {"claim_or_question", "variables_or_factors", "competing_explanations", "experimental_constraints"},
        "operations": {"design_experiment", "choose_controls", "design_comparison", "evaluate_hypothesis"},
    },
    "Result": {
        "fields": {"observations", "comparison_context", "interpretation_context"},
        "operations": {"interpret", "infer", "diagnose", "explain", "derive_design_implication", "compare_explanations"},
    },
    "Conclusion": {
        "fields": {"premises", "evidence", "scope_conditions"},
        "operations": {"infer", "assess", "synthesize", "justify", "critique", "calibrate_conclusion"},
    },
    "Hypothesis": {
        "fields": {"observed_phenomenon", "variables", "conditions"},
        "operations": {"formulate_hypothesis", "propose_mechanism", "predict", "relate_variables"},
    },
}


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise PipelineError(f"{label} fields mismatch: expected {sorted(expected)}, got {sorted(value)}")


def _confidence(value: Any, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise PipelineError(f"{label} must be a number in [0,1]")


def normalize_fragments(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, row in enumerate(rows, start=1):
        fragment_id = row.get("fragment_id", row.get("final_fragment_id"))
        text = row.get("text", row.get("fragment_text"))
        if not isinstance(fragment_id, str) or not fragment_id.strip():
            raise PipelineError(f"input row {position}: missing fragment_id/final_fragment_id")
        if fragment_id in seen:
            raise PipelineError(f"input row {position}: duplicate fragment ID {fragment_id}")
        if not isinstance(text, str) or not text.strip():
            raise PipelineError(f"input row {position}: missing text/fragment_text")
        actual_hash = sha256_text(text)
        supplied_hash = row.get("text_sha256", row.get("fragment_text_sha256"))
        if supplied_hash is not None and supplied_hash != actual_hash:
            raise PipelineError(f"input row {position}: text SHA-256 mismatch for {fragment_id}")
        markers = row.get("identity_markers", [])
        if not isinstance(markers, list) or any(not isinstance(item, str) for item in markers):
            raise PipelineError(f"input row {position}: identity_markers must be an array of strings")
        metadata = {
            key: value
            for key, value in row.items()
            if key not in {"fragment_id", "final_fragment_id", "text", "fragment_text", "text_sha256", "fragment_text_sha256", "identity_markers"}
        }
        normalized.append(
            {
                "fragment_id": fragment_id,
                "text": text,
                "text_sha256": actual_hash,
                "identity_markers": markers,
                "metadata": metadata,
            }
        )
        seen.add(fragment_id)
    if not normalized:
        raise PipelineError("input JSONL contains no fragments")
    return normalized


def validate_category_payload(payload: Mapping[str, Any], expected_ids: Sequence[str]) -> list[dict[str, Any]]:
    _exact_keys(payload, {"schema", "prompt_version", "annotations"}, "category response")
    if payload["schema"] != "fragment_category_annotations_batch_v1" or payload["prompt_version"] != "category_v1":
        raise PipelineError("category response schema/version mismatch")
    annotations = payload["annotations"]
    if not isinstance(annotations, list) or [item.get("fragment_id") for item in annotations if isinstance(item, Mapping)] != list(expected_ids):
        raise PipelineError("category response fragment IDs/order mismatch")
    for item in annotations:
        if not isinstance(item, Mapping):
            raise PipelineError("category annotation must be an object")
        _exact_keys(item, {"fragment_id", "primary_category", "secondary_category", "mixed_category", "confidence", "short_reason"}, "category annotation")
        primary, secondary = item["primary_category"], item["secondary_category"]
        if primary not in CATEGORIES or (secondary is not None and secondary not in CATEGORIES):
            raise PipelineError(f"invalid category for {item['fragment_id']}")
        if secondary == primary or item["mixed_category"] is not (secondary is not None):
            raise PipelineError(f"mixed/secondary category invariant failed for {item['fragment_id']}")
        _confidence(item["confidence"], "category confidence")
        if not isinstance(item["short_reason"], str) or not item["short_reason"].strip():
            raise PipelineError("category short_reason must be non-empty")
    return [dict(item) for item in annotations]


def validate_eligibility_payload(payload: Mapping[str, Any], expected_ids: Sequence[str]) -> list[dict[str, Any]]:
    _exact_keys(payload, {"schema", "prompt_version", "annotations"}, "eligibility response")
    if payload["schema"] != "fragment_task_eligibility_batch_v1" or payload["prompt_version"] != "eligibility_v1":
        raise PipelineError("eligibility response schema/version mismatch")
    annotations = payload["annotations"]
    if not isinstance(annotations, list) or [item.get("fragment_id") for item in annotations if isinstance(item, Mapping)] != list(expected_ids):
        raise PipelineError("eligibility response fragment IDs/order mismatch")
    expected_fields = {
        "fragment_id", "primary_category", "secondary_category", "task_eligibility",
        "exclusion_reason", "borderline_reason", "confidence", "taskability_dimensions",
        "latent_task_operation", "short_reason",
    }
    for item in annotations:
        if not isinstance(item, Mapping):
            raise PipelineError("eligibility annotation must be an object")
        _exact_keys(item, expected_fields, "eligibility annotation")
        decision = item["task_eligibility"]
        if decision not in ELIGIBILITIES:
            raise PipelineError(f"invalid task eligibility for {item['fragment_id']}")
        exclusion, borderline = item["exclusion_reason"], item["borderline_reason"]
        if decision == "keep" and (exclusion is not None or borderline is not None):
            raise PipelineError("keep reason invariant failed")
        if decision == "exclude" and (not isinstance(exclusion, str) or borderline is not None):
            raise PipelineError("exclude reason invariant failed")
        if decision == "borderline" and (exclusion is not None or not isinstance(borderline, str)):
            raise PipelineError("borderline reason invariant failed")
        dimensions = item["taskability_dimensions"]
        if not isinstance(dimensions, Mapping) or set(dimensions) != set(TASKABILITY_DIMENSIONS) or any(type(value) is not bool for value in dimensions.values()):
            raise PipelineError("taskability_dimensions invariant failed")
        _confidence(item["confidence"], "eligibility confidence")
    return [dict(item) for item in annotations]


def _find_forbidden_key(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in FORBIDDEN_ANSWER_KEYS:
                return str(key)
            found = _find_forbidden_key(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_forbidden_key(child)
            if found:
                return found
    return None


def normalize_prompt(text: str) -> str:
    return " ".join(TOKEN_RE.findall(text.lower()))


def count_english_words(text: str) -> int:
    return len(WORD_RE.findall(text))


def count_cjk_characters(text: str) -> int:
    """Count Han characters as Unicode code points, independent of encoding."""
    return len(CJK_RE.findall(text))


def detect_text_language(text: str) -> str:
    """Return ``zh`` for predominantly Chinese text, otherwise ``en``.

    This deliberately small heuristic handles mixed Chinese technical prose without
    requiring a tokenizer or language-detection dependency.
    """
    cjk_count = count_cjk_characters(text)
    english_words = count_english_words(text)
    return "zh" if cjk_count >= 8 and cjk_count >= 2 * english_words else "en"


def resolve_task_language(mode: str, fragment_text: str) -> str:
    if mode not in {"auto", "en", "zh"}:
        raise PipelineError("task language must be 'auto', 'en', or 'zh'")
    return detect_text_language(fragment_text) if mode == "auto" else mode


def _similarity_features(text: str) -> set[str]:
    tokens = TOKEN_RE.findall(text.lower())
    if count_cjk_characters(text) >= 4 and len(tokens) >= 3:
        return {" ".join(tokens[index : index + 3]) for index in range(len(tokens) - 2)}
    return set(tokens)


def token_jaccard(left: str, right: str) -> float:
    a, b = _similarity_features(left), _similarity_features(right)
    return len(a & b) / len(a | b) if a or b else 1.0


def answer_core_literal_leakage(prompt: str, core_items: Sequence[str]) -> bool:
    prompt_norm = normalize_prompt(prompt)
    prompt_tokens = prompt_norm.split()
    for item in core_items:
        core = normalize_prompt(item)
        tokens = core.split()
        if len(tokens) >= 5 and core in prompt_norm:
            return True
        width = min(8, len(tokens))
        if width >= 6:
            grams = {" ".join(tokens[i : i + width]) for i in range(len(tokens) - width + 1)}
            prompt_grams = {" ".join(prompt_tokens[i : i + width]) for i in range(len(prompt_tokens) - width + 1)}
            if grams & prompt_grams:
                return True
    return False


def validate_generation_payload(
    payload: Mapping[str, Any], expected_ids: Sequence[str], candidate_count: int
) -> list[dict[str, Any]]:
    _exact_keys(payload, {"schema", "fragments"}, "generation response")
    if payload["schema"] != "task_reverse_generation_batch_v1":
        raise PipelineError("generation response schema mismatch")
    fragments = payload["fragments"]
    if not isinstance(fragments, list) or [item.get("fragment_id") for item in fragments if isinstance(item, Mapping)] != list(expected_ids):
        raise PipelineError("generation response fragment IDs/order mismatch")
    expected_indexes = list(range(1, candidate_count + 1))
    for item in fragments:
        _exact_keys(item, {"fragment_id", "task_candidates", "failure_reasons"}, "generation fragment")
        candidates = item["task_candidates"]
        if not isinstance(candidates, list) or [candidate.get("candidate_index") for candidate in candidates if isinstance(candidate, Mapping)] != expected_indexes:
            raise PipelineError(f"generation candidate indexes mismatch for {item['fragment_id']}")
        if not isinstance(item["failure_reasons"], list):
            raise PipelineError("generation failure_reasons must be an array")
        for candidate in candidates:
            if _find_forbidden_key(candidate):
                raise PipelineError("generation output contains a forbidden answer/rubric field")
    return [dict(item) for item in fragments]


def validate_candidate(
    fragment: Mapping[str, Any],
    category: str,
    candidate: Mapping[str, Any],
    *,
    min_words: int,
    max_words: int,
    task_language: str = "auto",
    min_cjk_chars: int = 40,
    max_cjk_chars: int = 320,
    prior_candidates: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    reasons: list[str] = []
    required = {
        "candidate_index", "task_variant_mode", "task_spec", "answer_core_to_withhold",
        "task_prompt", "generation_checks", "generation_confidence", "short_note",
    }
    if set(candidate) != required:
        reasons.append("candidate_fields_mismatch")
    prompt = candidate.get("task_prompt", "")
    if not isinstance(prompt, str) or not prompt.strip():
        reasons.append("empty_task_prompt")
        prompt = ""
    word_count = count_english_words(prompt)
    cjk_count = count_cjk_characters(prompt)
    resolved_language = resolve_task_language(
        task_language, str(fragment.get("text", ""))
    )
    if resolved_language == "zh":
        task_length = cjk_count
        minimum_length, maximum_length = min_cjk_chars, max_cjk_chars
        length_unit = "cjk_characters"
    else:
        task_length = word_count
        minimum_length, maximum_length = min_words, max_words
        length_unit = "english_words"
    if task_length < minimum_length:
        reasons.append("task_prompt_too_short")
    if task_length > maximum_length:
        reasons.append("task_prompt_too_long")
    if SOURCE_REFERENCE_RE.search(prompt):
        reasons.append("source_reference_detected")
    for marker in fragment.get("identity_markers", []):
        if marker.strip() and marker.casefold() in prompt.casefold():
            reasons.append("identity_marker_detected")
            break
    spec = candidate.get("task_spec")
    if not isinstance(spec, Mapping) or set(spec) != {"structure_type", "fields", "requested_operation"}:
        reasons.append("invalid_task_spec")
        spec = {}
    if spec.get("structure_type") != category:
        reasons.append("structure_type_mismatch")
    fields = spec.get("fields")
    names: list[str] = []
    if not isinstance(fields, list) or not fields:
        reasons.append("missing_task_spec_fields")
    else:
        for field in fields:
            if not isinstance(field, Mapping) or set(field) != {"name", "items"}:
                reasons.append("invalid_task_spec_field")
                continue
            name, items = field["name"], field["items"]
            if name not in CATEGORY_RULES[category]["fields"]:
                reasons.append("invalid_category_field")
            else:
                names.append(name)
            if not isinstance(items, list) or not items or any(not isinstance(value, str) or not value.strip() for value in items):
                reasons.append("invalid_category_field_items")
    if len(names) != len(set(names)):
        reasons.append("duplicate_category_field")
    if spec.get("requested_operation") not in CATEGORY_RULES[category]["operations"]:
        reasons.append("invalid_requested_operation")
    variant = candidate.get("task_variant_mode")
    if not isinstance(variant, str) or not variant.strip():
        reasons.append("missing_task_variant_mode")
    core = candidate.get("answer_core_to_withhold")
    if not isinstance(core, list) or not core or any(not isinstance(item, str) or not item.strip() for item in core):
        reasons.append("missing_answer_core_to_withhold")
        core = []
    checks = candidate.get("generation_checks")
    if not isinstance(checks, Mapping) or set(checks) != set(GENERATION_CHECKS):
        reasons.append("invalid_generation_checks")
    else:
        for key, expected in EXPECTED_CHECKS.items():
            if checks.get(key) is not expected:
                reasons.append(f"generation_check_failed_{key}")
    try:
        _confidence(candidate.get("generation_confidence"), "generation confidence")
    except PipelineError:
        reasons.append("invalid_generation_confidence")
    if not isinstance(candidate.get("short_note"), str) or not candidate.get("short_note", "").strip():
        reasons.append("missing_short_note")
    if core and answer_core_literal_leakage(prompt, core):
        reasons.append("literal_leakage")
    prompt_norm = normalize_prompt(prompt)
    for prior in prior_candidates:
        other = str(prior.get("task_prompt", ""))
        other_norm = normalize_prompt(other)
        if prompt_norm == other_norm:
            reasons.append("exact_task_prompt_duplicate")
        if token_jaccard(prompt, other) >= 0.82 or difflib.SequenceMatcher(None, prompt_norm, other_norm).ratio() >= 0.90:
            reasons.append("candidate_near_duplicate")
        if variant == prior.get("task_variant_mode"):
            reasons.append("candidate_variant_duplicate")
    return {
        "valid": not reasons,
        "reason_codes": sorted(set(reasons)),
        "task_language": resolved_language,
        "task_prompt_length": task_length,
        "task_prompt_length_unit": length_unit,
        "task_prompt_word_count": word_count,
        "task_prompt_cjk_character_count": cjk_count,
        "task_prompt_sha256": sha256_text(prompt),
        "normalized_task_prompt_sha256": sha256_text(prompt_norm),
    }


def validate_leakage_payload(payload: Mapping[str, Any], expected_pairs: Sequence[tuple[int, int]]) -> list[dict[str, Any]]:
    _exact_keys(payload, {"schema", "validations"}, "leakage response")
    if payload["schema"] != "task_leakage_validation_batch_v1":
        raise PipelineError("leakage response schema mismatch")
    validations = payload["validations"]
    actual = [(item.get("batch_item_index"), item.get("candidate_index")) for item in validations if isinstance(item, Mapping)] if isinstance(validations, list) else []
    if actual != list(expected_pairs):
        raise PipelineError("leakage response indexes/order mismatch")
    for item in validations:
        _exact_keys(item, {"batch_item_index", "candidate_index", "validation", "failure_types", "short_reason"}, "leakage validation")
        decision, failures = item["validation"], item["failure_types"]
        if decision not in {"pass", "fail"} or not isinstance(failures, list) or any(value not in LEAKAGE_FAILURE_TYPES for value in failures):
            raise PipelineError("invalid leakage validation decision/failure type")
        if (decision == "pass" and failures) or (decision == "fail" and not failures):
            raise PipelineError("leakage validation failure invariant failed")
    return [dict(item) for item in validations]
