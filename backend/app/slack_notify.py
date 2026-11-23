"""Slack notification sent when a packing plan is saved (Save & Finish).

Configuration (environment variables, first match wins):
  - SLACK_WEBHOOK_URL: Incoming Webhook URL (posts to the channel chosen when the webhook was created).
  - SLACK_BOT_TOKEN + SLACK_CHANNEL_ID: bot token (xoxb-, scope chat:write) posting via chat.postMessage.
If neither is set the notification is skipped. Failures never block saving the plan.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any, Callable

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 5
MAX_ITEM_LINES = 25

Sender = Callable[[str, dict[str, Any], dict[str, str]], dict[str, Any]]


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8", **headers}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310 - fixed Slack URLs
        body = response.read().decode("utf-8", "replace")
    if body.strip() == "ok":  # Incoming webhooks answer with plain "ok".
        return {"ok": True}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"ok": False, "error": body[:200]}


def _fmt(value: Any) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


def build_message(order: dict[str, Any]) -> dict[str, Any]:
    """Block Kit message: package ready to pack + packing list summary."""
    result = order["plan"]["result"]
    boxes = result.get("boxes") or []
    title = f"📦 Order {order['order_number']} is ready to pack"
    lines: list[str] = []
    for box in boxes:
        dims = " x ".join(_fmt(v) for v in box.get("dimensions_cm", []))
        lines.append(f"*Box {box['sequence']} – {box['box_code']}* ({dims} cm, {_fmt(box.get('gross_weight_kg', 0))} kg)")
        for item in box.get("items", []):
            lines.append(f"   • {item['quantity']} × {item['name']}")
    if len(lines) > MAX_ITEM_LINES:
        hidden = len(lines) - MAX_ITEM_LINES
        lines = lines[:MAX_ITEM_LINES] + [f"_…and {hidden} more lines (see the saved PDF)_"]
    packing_list = "\n".join(lines) or "_No boxes in the plan._"
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": title[:150], "emoji": True}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Client:*\n{order.get('client') or '—'}"},
            {"type": "mrkdwn", "text": f"*Boxes:*\n{result.get('box_count', len(boxes))}"},
            {"type": "mrkdwn", "text": f"*Units:*\n{result.get('unit_count', '—')}"},
            {"type": "mrkdwn", "text": f"*Total weight:*\n{_fmt(result.get('total_weight_kg', 0))} kg"},
        ]},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Packing list*\n{packing_list}"[:3000]}},
    ]
    return {"text": title, "blocks": blocks}


def notify_plan_ready(order: dict[str, Any], sender: Sender = _post_json) -> str:
    """Send the notification. Returns 'sent', 'skipped' (not configured) or 'failed'."""
    webhook = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    channel = os.getenv("SLACK_CHANNEL_ID", "").strip()
    message = build_message(order)
    try:
        if webhook:
            response = sender(webhook, message, {})
        elif token and channel:
            response = sender("https://slack.com/api/chat.postMessage", {"channel": channel, **message},
                              {"Authorization": f"Bearer {token}"})
        else:
            return "skipped"
    except Exception as error:  # Network/HTTP errors must not break Save & Finish.
        logger.warning("Slack notification failed for order %s: %s", order.get("order_number"), error)
        return "failed"
    if not response.get("ok"):
        logger.warning("Slack rejected notification for order %s: %s", order.get("order_number"), response.get("error"))
        return "failed"
    return "sent"
