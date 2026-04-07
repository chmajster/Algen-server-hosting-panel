from __future__ import annotations

from datetime import datetime
import json
from typing import Any

from panel.extensions import db
from panel.models import Client, PolicyDocument, PolicyEvaluation, User


class PolicyViolationError(RuntimeError):
    def __init__(self, message: str, *, evaluations: list[dict] | None = None):
        super().__init__(message)
        self.evaluations = evaluations or []


POLICY_STATES = {"draft", "active", "archived"}


def _policy_meta(definition: dict | None) -> dict:
    if not isinstance(definition, dict):
        return {}
    meta = definition.get("_meta")
    if isinstance(meta, dict):
        return dict(meta)
    return {}


def policy_state(policy: PolicyDocument) -> str:
    definition = dict(policy.definition_json or {})
    meta = _policy_meta(definition)
    state = str(meta.get("state") or "").strip().lower()
    if state in POLICY_STATES:
        return state
    return "active" if bool(policy.is_active) else "draft"


def validate_policy_definition(definition: dict) -> list[str]:
    errors: list[str] = []
    rules = list(definition.get("rules") or [])
    for idx, rule in enumerate(rules):
        if not isinstance(rule, dict):
            errors.append(f"Rule #{idx + 1} musi byc obiektem.")
            continue
        effect = str(rule.get("effect") or "allow").strip().lower()
        if effect not in {"allow", "warn", "deny"}:
            errors.append(f"Rule #{idx + 1} ma nieobslugiwany effect: {effect}")
        event = str(rule.get("event") or "").strip()
        if not event:
            errors.append(f"Rule #{idx + 1} musi miec event.")
        when_obj = rule.get("when")
        if when_obj is not None and not isinstance(when_obj, dict):
            errors.append(f"Rule #{idx + 1} pole when musi byc obiektem.")
    return errors


def parse_policy_definition(raw_text: str | None) -> dict:
    text = (raw_text or "").strip()
    if not text:
        return {"rules": []}
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("Definicja polityki musi byc obiektem JSON.")
    rules = parsed.get("rules")
    if rules is None:
        parsed["rules"] = []
    elif not isinstance(rules, list):
        raise ValueError("Pole rules musi byc lista.")
    return parsed


def _set_policy_state(
    policy: PolicyDocument,
    *,
    state: str,
    actor: User | None,
    reason: str | None = None,
    previous_active_policy_id: int | None = None,
    rollback_from_policy_id: int | None = None,
) -> PolicyDocument:
    normalized = (state or "").strip().lower()
    if normalized not in POLICY_STATES:
        raise ValueError("Nieznany stan policy.")

    definition = dict(policy.definition_json or {})
    meta = _policy_meta(definition)
    meta["state"] = normalized
    meta["updated_at"] = datetime.utcnow().isoformat()
    if reason:
        meta["state_reason"] = (reason or "")[:255]
    if previous_active_policy_id is not None:
        meta["previous_active_policy_id"] = int(previous_active_policy_id)
    if rollback_from_policy_id is not None:
        meta["rollback_from_policy_id"] = int(rollback_from_policy_id)
    definition["_meta"] = meta

    policy.definition_json = definition
    policy.is_active = normalized == "active"
    policy.updated_by = actor
    return policy


def activate_policy(policy: PolicyDocument, *, actor: User | None) -> PolicyDocument:
    definition = dict(policy.definition_json or {})
    errors = validate_policy_definition(definition)
    if errors:
        raise ValueError("; ".join(errors))

    query = PolicyDocument.query.filter(PolicyDocument.id != policy.id)
    if policy.scope == "tenant":
        query = query.filter(PolicyDocument.scope == "tenant", PolicyDocument.client_id == policy.client_id)
    else:
        query = query.filter(PolicyDocument.scope == "global")

    active_rows = query.filter(PolicyDocument.is_active.is_(True)).order_by(PolicyDocument.id.desc()).all()
    previous_active_policy_id = active_rows[0].id if active_rows else None
    for row in active_rows:
        _set_policy_state(row, state="archived", actor=actor, reason=f"superseded_by:{policy.id}")

    _set_policy_state(
        policy,
        state="active",
        actor=actor,
        reason="activation",
        previous_active_policy_id=previous_active_policy_id,
    )
    return policy


def archive_policy(policy: PolicyDocument, *, actor: User | None) -> PolicyDocument:
    return _set_policy_state(policy, state="archived", actor=actor, reason="manual_archive")


def rollback_policy(policy: PolicyDocument, *, actor: User | None) -> PolicyDocument:
    definition = dict(policy.definition_json or {})
    meta = _policy_meta(definition)
    previous_id = meta.get("previous_active_policy_id")

    target = None
    if isinstance(previous_id, int):
        target = PolicyDocument.query.get(previous_id)

    if target is None:
        query = PolicyDocument.query.filter(PolicyDocument.id != policy.id, PolicyDocument.is_active.is_(False))
        if policy.scope == "tenant":
            query = query.filter(PolicyDocument.scope == "tenant", PolicyDocument.client_id == policy.client_id)
        else:
            query = query.filter(PolicyDocument.scope == "global")
        target = query.order_by(PolicyDocument.updated_at.desc(), PolicyDocument.id.desc()).first()

    if target is None:
        raise ValueError("Brak polityki do rollback.")

    _set_policy_state(policy, state="archived", actor=actor, reason=f"rollback_to:{target.id}")
    activate_policy(target, actor=actor)
    _set_policy_state(
        target,
        state="active",
        actor=actor,
        reason=f"rollback_from:{policy.id}",
        rollback_from_policy_id=policy.id,
    )
    return target


def _compare(actual: Any, expected: Any, operator: str) -> bool:
    op = (operator or "eq").strip().lower()
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "in":
        return actual in (expected if isinstance(expected, list) else [expected])
    if op == "not_in":
        return actual not in (expected if isinstance(expected, list) else [expected])
    if op == "contains":
        if isinstance(actual, (list, tuple, set)):
            return expected in actual
        if isinstance(actual, str):
            return str(expected) in actual
        return False
    if op == "gt":
        return actual is not None and expected is not None and actual > expected
    if op == "lt":
        return actual is not None and expected is not None and actual < expected
    if op == "exists":
        return actual is not None
    return actual == expected


def _condition_matches(condition: dict | None, context: dict) -> bool:
    if not condition:
        return True

    all_match = condition.get("all") if isinstance(condition, dict) else None
    if isinstance(all_match, list):
        return all(_condition_matches(item, context) for item in all_match if isinstance(item, dict))

    any_match = condition.get("any") if isinstance(condition, dict) else None
    if isinstance(any_match, list):
        return any(_condition_matches(item, context) for item in any_match if isinstance(item, dict))

    field = str(condition.get("field") or "").strip()
    if not field:
        return True

    operator = str(condition.get("operator") or "eq").strip().lower()
    expected = condition.get("value")
    actual = context.get(field)
    return _compare(actual, expected, operator)


def _iter_policies(*, client: Client | None, include_inactive: bool = False) -> list[PolicyDocument]:
    query = PolicyDocument.query
    if not include_inactive:
        query = query.filter_by(is_active=True)
    if client is None:
        query = query.filter(PolicyDocument.scope == "global")
    else:
        query = query.filter(
            (PolicyDocument.scope == "global")
            | ((PolicyDocument.scope == "tenant") & (PolicyDocument.client_id == client.id))
        )
    return query.order_by(PolicyDocument.created_at.asc(), PolicyDocument.id.asc()).all()


def evaluate_policies(
    *,
    event_type: str,
    context: dict,
    client: Client | None,
    actor: User | None,
    target_type: str | None = None,
    target_id: str | int | None = None,
    persist: bool = True,
    policy_ids: list[int] | None = None,
    include_inactive: bool = False,
) -> dict:
    event_name = (event_type or "").strip()
    policies = _iter_policies(client=client, include_inactive=include_inactive)
    decisions: list[dict] = []
    blocked = False
    policy_id_set = {int(item) for item in (policy_ids or []) if str(item).isdigit()}

    for policy in policies:
        if policy_id_set and policy.id not in policy_id_set:
            continue
        if not include_inactive and policy_state(policy) != "active":
            continue

        definition = dict(policy.definition_json or {})
        rules = list(definition.get("rules") or [])
        for idx, rule in enumerate(rules):
            if not isinstance(rule, dict):
                continue

            rule_event = str(rule.get("event") or "").strip()
            if rule_event and rule_event != event_name:
                continue

            when_condition = rule.get("when") if isinstance(rule.get("when"), dict) else {}
            if not _condition_matches(when_condition, context):
                continue

            decision = str(rule.get("effect") or "allow").strip().lower()
            if decision not in {"allow", "warn", "deny"}:
                decision = "allow"

            message = str(rule.get("message") or f"Policy {policy.name} matched rule #{idx + 1}").strip()[:255]
            enforced = policy.enforcement_mode == "enforce"
            if decision == "deny" and enforced:
                blocked = True

            item = {
                "policy_id": policy.id,
                "policy_name": policy.name,
                "rule_index": idx,
                "decision": decision,
                "message": message,
                "enforcement_mode": policy.enforcement_mode,
                "enforced": enforced,
            }
            decisions.append(item)

            if persist:
                db.session.add(
                    PolicyEvaluation(
                        policy=policy,
                        client=client,
                        event_type=event_name,
                        target_type=(target_type or "").strip()[:64] or None,
                        target_id=str(target_id)[:120] if target_id is not None else None,
                        decision=decision,
                        message=message,
                        input_json=context,
                        evaluated_by=actor,
                    )
                )

    return {
        "event_type": event_name,
        "blocked": blocked,
        "decisions": decisions,
        "matched": len(decisions),
    }


def enforce_policies(
    *,
    event_type: str,
    context: dict,
    client: Client | None,
    actor: User | None,
    target_type: str | None = None,
    target_id: str | int | None = None,
) -> dict:
    result = evaluate_policies(
        event_type=event_type,
        context=context,
        client=client,
        actor=actor,
        target_type=target_type,
        target_id=target_id,
        persist=True,
    )
    if result["blocked"]:
        message = "; ".join(item["message"] for item in result["decisions"] if item["decision"] == "deny")
        raise PolicyViolationError(message or "Polityka zabrania wykonania tej akcji.", evaluations=result["decisions"])
    return result
