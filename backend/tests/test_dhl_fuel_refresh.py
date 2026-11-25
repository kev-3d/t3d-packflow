import tempfile
import unittest
from datetime import date
from pathlib import Path

from app import database, dhl_surcharges
from app.dhl_surcharges import (
    ensure_fuel_for, fuel_for, html_to_text, load_surcharges, parse_fuel_text, refresh_fuel_from_web,
    reset_web_cache,
)

# Trimmed copy of the markup on https://mydhl.express.dhl/mt/en/ship/surcharges.html (AEM tables).
PAGE = """
<html><head><script>var nav = "November 1-2, 2030 99.00%";</script><style>.x{}</style></head><body>
<div class="v2-p"><strong>International Time Definite services</strong></div>
<table class="dhl-tables-component"><tbody>
 <tr><th scope="col"><div><div class="v2-p"><strong>Effective Date</strong></div></div></th>
     <th scope="col"><div class="v2-p"><strong>Surcharges</strong></div></th></tr>
 <tr><td><div class="v2-p">November 30-December&nbsp;6, 2026</div></td><td><div class="v2-p">48.00%</div></td></tr>
 <tr><td><div class="v2-p">November 23-29, 2026</div></td><td><div class="v2-p">49.25%</div></td></tr>
 <tr><td><div class="v2-p">December 28-January 3, 2027</div></td><td><div class="v2-p">47.50%</div></td></tr>
</tbody></table>
<div class="v2-p"><strong>Domestic Time Definite services</strong></div>
<table><tr><td>November 30-December 6, 2026</td><td>37.50%</td></tr></table>
</body></html>
"""


class FuelPageParsingTests(unittest.TestCase):
    def test_reads_only_the_international_table_from_the_html(self):
        weeks = parse_fuel_text(html_to_text(PAGE))
        self.assertEqual(weeks, [
            {"from": "2026-11-23", "to": "2026-11-29", "pct": 49.25},
            {"from": "2026-11-30", "to": "2026-12-06", "pct": 48.0},
            {"from": "2026-12-28", "to": "2027-01-03", "pct": 47.5},
        ])

    def test_a_page_without_the_table_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_fuel_text(html_to_text("<html><body>Maintenance</body></html>"))


class FuelRefreshTests(unittest.TestCase):
    def setUp(self):
        self.original = database.DB_PATH
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        database.DB_PATH = Path(self.tmp.name) / "fuel.db"
        database.init_db()
        reset_web_cache()

    def tearDown(self):
        reset_web_cache()
        database.DB_PATH = self.original
        self.tmp.cleanup()

    def test_refresh_stores_weeks_and_merges_them_over_the_bundled_schedule(self):
        bundled_first = load_surcharges()["fuel"]["weeks"][0]
        self.assertIsNone(fuel_for(date(2026, 11, 25)))
        self.assertEqual(refresh_fuel_from_web(lambda: PAGE)["status"], "updated")
        fuel = load_surcharges()["fuel"]
        self.assertEqual(fuel["source"], "DHL website")
        self.assertEqual(fuel_for(date(2026, 11, 25))["pct"], 49.25)
        self.assertEqual(fuel["weeks"][0], bundled_first)  # history from the bundled JSON is kept
        # Stored in the database: a fresh process (empty memory) still sees the fetched weeks.
        reset_web_cache()
        self.assertEqual(fuel_for(date(2026, 12, 1))["pct"], 48.0)
        self.assertEqual(refresh_fuel_from_web(lambda: PAGE)["status"], "unchanged")

    def test_a_failed_fetch_keeps_the_previous_weeks(self):
        refresh_fuel_from_web(lambda: PAGE)

        def offline():
            raise OSError("network down")
        self.assertEqual(refresh_fuel_from_web(offline)["status"], "failed")
        self.assertEqual(refresh_fuel_from_web(lambda: "<html>changed layout</html>")["status"], "failed")
        self.assertEqual(fuel_for(date(2026, 11, 25))["pct"], 49.25)

    def test_on_demand_refresh_runs_only_when_the_week_is_missing_and_at_most_hourly(self):
        calls = []

        def fetch():
            calls.append(1)
            return PAGE
        ensure_fuel_for(date(2026, 11, 25), fetch)
        ensure_fuel_for(date(2026, 11, 25), fetch)  # now covered: no fetch
        ensure_fuel_for(date(2027, 6, 1), fetch)    # still missing, but tried within the hour: no fetch
        self.assertEqual(len(calls), 1)
        dhl_surcharges._last_attempt -= dhl_surcharges.ON_DEMAND_MIN_INTERVAL_SECONDS + 1
        ensure_fuel_for(date(2027, 6, 1), fetch)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
