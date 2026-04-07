from __future__ import annotations

import hashlib
import json
from datetime import datetime

from panel.extensions import db
from panel.models import AutomationExecution, AutomationRule, Client, User
from panel.services.audit import log_activity
from panel.services.webhooks import dispatch_webhook_event


def parse_json_text(raw: str | None, *, default):
    text = (raw or "").strip()
    if not text:
        return default
    return json.loads(text)


def _fingerprint(*, rule_id: int, trigger_event: str, payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    digest = hashlib.sha256(f"{rule_id}:{trigger_event}:{body}".encode("utf-8")).hexdigest()
    return digest


def _conditions_match(conditions: dict | None, payload: dict) -> bool:
    if not conditions:
        return True

    all_match = conditions.get("all")
    if isinstance(all_match, list):
        return all(_conditions_match(item, payload) for item in all_match if isinstance(item, dict))

    any_match = conditions.get("any")
    if isinstance(any_match, list):
        return any(_conditions_match(item, payload) for item in any_match if isinstance(item, dict))

    field = str(conditions.get("field") or "").strip()
    expected = conditions.get("equals")
    if not field:
        return True
    return payload.get(field) == expected


def _run_actions(rule: AutomationRule, payload: dict, *, client: Client | None, actor: User | None) -> list[str]:
    results: list[str] = []
    actions = list(rule.actions_json or [])
    for action in actions:
        if isinstance(action, str):
            action = {"type": action}
        if not isinstance(action, dict):
            continue

        action_type = str(action.get("type") or "").strip().lower()
        if action_type == "log":
            message = str(action.get("message") or f"Automation rule {rule.name} executed")[:255]
            log_activity(
                "automation.rule_log",
                "automation_rule",
                message,
                entity_id=rule.id,
                actor=actor,
                client=client,
                metadata={"trigger_event": rule.trigger_event, "payload": payload},
            )
            results.append("log")
            continue

        if action_type == "webhook":
            webhook_event = str(action.get("event") or rule.trigger_event).strip() or rule.trigger_event
            custom_payload = action.get("payload")
            if isinstance(custom_payload, dict):
                webhook_payload = dict(custom_payload)
                webhook_payload.setdefault("automation_payload", payload)
            else:
                webhook_payload = dict(payload)
            dispatch_webhook_event(
                webhook_event,
                webhook_payload,
                client=client,
                auto_commit=False,
            )
            results.append("webhook")
            continue

        if action_type == "set_client_billing_status":
            if client is None:
                continue
            status = str(action.get("status") or "").strip()
            if status:
                client.billing_status = status
                db.session.add(client)
                results.append("set_client_billing_status")
            continue

    return results


def execute_automation_rules(
    *,
    trigger_event: str,
    payload: dict | None = None,
    client: Client | None = None,
    actor: User | None = None,
) -> dict[str, int]:
    event = (trigger_event or "").strip()
    if not event:
        return {"matched": 0, "executed": 0, "failed": 0, "skipped": 0}

    event_payload = dict(payload or {})
    rules = AutomationRule.query.filter_by(is_active=True, trigger_event=event).order_by(AutomationRule.created_at.asc()).all()
    matched = 0
    executed = 0
    failed = 0
    skipped = 0

    for rule in rules:
        if not _conditions_match(rule.conditions_json or {}, event_payload):
            continue

        matched += 1
        fingerprint = _fingerprint(rule_id=rule.id, trigger_event=event, payload=event_payload)
        duplicate = AutomationExecution.query.filter_by(rule_id=rule.id, event_fingerprint=fingerprint).first()
        if duplicate is not None:
            skipped += 1
            continue

        execution = AutomationExecution(
            rule=rule,
            trigger_event=event,
            event_fingerprint=fingerprint,
            status="running",
            message="Rule matched",
            metadata_json={"payload": event_payload, "started_at": datetime.utcnow().isoformat()},
        )
        db.session.add(execution)
        db.session.flush()

        try:
            action_results = _run_actions(rule, event_payload, client=client, actor=actor)
            execution.status = "success"
            execution.message = f"Executed actions: {', '.join(action_results) if action_results else 'none'}"
            metadata = dict(execution.metadata_json or {})
            metadata["actions"] = action_results
            metadata["finished_at"] = datetime.utcnow().isoformat()
            execution.metadata_json = metadata
            executed += 1
        except Exception as exc:
            execution.status = "failed"
            execution.message = str(exc)[:500]
            metadata = dict(execution.metadata_json or {})
            metadata["finished_at"] = datetime.utcnow().isoformat()
            execution.metadata_json = metadata
            failed += 1

        if rule.stop_on_match:
            break

    return {"matched": matched, "executed": executed, "failed": failed, "skipped": skipped}
