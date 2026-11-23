import os
import unittest
from unittest import mock

from app.slack_notify import build_message, notify_plan_ready

ORDER = {
    "order_number": "PO-123", "client": "Acme", "requested_date": "2026-11-20",
    "plan": {"result": {
        "status": "complete", "box_count": 2, "unit_count": 5, "total_weight_kg": 7.5, "average_utilization": 81.2,
        "boxes": [
            {"sequence": 1, "box_code": "B40", "dimensions_cm": [40.0, 30.0, 20.0], "gross_weight_kg": 4.5,
             "items": [{"name": "Magigoo Pro PP", "quantity": 3}]},
            {"sequence": 2, "box_code": "B20", "dimensions_cm": [20.0, 20.0, 20.0], "gross_weight_kg": 3.0,
             "items": [{"name": "Magigoo Original", "quantity": 2}]},
        ],
    }},
}

CLEAN_ENV = {"SLACK_WEBHOOK_URL": "", "SLACK_BOT_TOKEN": "", "SLACK_CHANNEL_ID": ""}


class SlackNotifyTests(unittest.TestCase):
    def test_message_contains_ready_status_and_packing_list(self):
        message = build_message(ORDER)
        self.assertIn("PO-123 is ready to pack", message["text"])
        body = str(message["blocks"])
        for expected in ["Acme", "Box 1 – B40", "3 × Magigoo Pro PP", "2 × Magigoo Original", "7.5 kg", "81.2%"]:
            self.assertIn(expected, body)

    def test_skipped_when_not_configured(self):
        sender = mock.Mock()
        with mock.patch.dict(os.environ, CLEAN_ENV):
            self.assertEqual(notify_plan_ready(ORDER, sender), "skipped")
        sender.assert_not_called()

    def test_webhook_used_when_configured(self):
        sender = mock.Mock(return_value={"ok": True})
        with mock.patch.dict(os.environ, {**CLEAN_ENV, "SLACK_WEBHOOK_URL": "https://hooks.slack.test/x"}):
            self.assertEqual(notify_plan_ready(ORDER, sender), "sent")
        url, payload, headers = sender.call_args.args
        self.assertEqual(url, "https://hooks.slack.test/x")
        self.assertNotIn("channel", payload)
        self.assertEqual(headers, {})

    def test_bot_token_posts_to_channel(self):
        sender = mock.Mock(return_value={"ok": True})
        with mock.patch.dict(os.environ, {**CLEAN_ENV, "SLACK_BOT_TOKEN": "xoxb-test", "SLACK_CHANNEL_ID": "C1"}):
            self.assertEqual(notify_plan_ready(ORDER, sender), "sent")
        url, payload, headers = sender.call_args.args
        self.assertEqual(url, "https://slack.com/api/chat.postMessage")
        self.assertEqual(payload["channel"], "C1")
        self.assertEqual(headers["Authorization"], "Bearer xoxb-test")

    def test_errors_never_raise(self):
        env = {**CLEAN_ENV, "SLACK_WEBHOOK_URL": "https://hooks.slack.test/x"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(notify_plan_ready(ORDER, mock.Mock(side_effect=OSError("down"))), "failed")
            self.assertEqual(notify_plan_ready(ORDER, mock.Mock(return_value={"ok": False, "error": "channel_not_found"})), "failed")


if __name__ == "__main__":
    unittest.main()
