from datetime import date, datetime, timedelta, timezone
import unittest
from backend.analytics_window import analytics_window


class AnalyticsWindowTests(unittest.TestCase):
    now = datetime(2026, 11, 16, 5, 12, tzinfo=timezone.utc)

    def test_default_preserves_seven_completed_singapore_days(self):
        start, end = analytics_window(now=self.now)
        self.assertEqual(end, datetime(2026, 11, 15, 16, tzinfo=timezone.utc))
        self.assertEqual(end-start, timedelta(days=7))

    def test_today_starts_at_singapore_midnight_and_stops_at_now(self):
        start, end = analytics_window(date(2026,11,16),date(2026,11,16),self.now)
        self.assertEqual(start,datetime(2026,11,15,16,tzinfo=timezone.utc))
        self.assertEqual(end,self.now)

    def test_end_date_is_inclusive(self):
        start, end = analytics_window(date(2026,11,10),date(2026,11,12),self.now)
        self.assertEqual(end-start,timedelta(days=3))
        self.assertEqual(end,datetime(2026,11,12,16,tzinfo=timezone.utc))

    def test_31_days_allowed_and_32_rejected(self):
        a,b=analytics_window(date(2026,10,16),date(2026,11,15),self.now)
        self.assertEqual(b-a,timedelta(days=31))
        with self.assertRaisesRegex(ValueError,'31'):
            analytics_window(date(2026,10,15),date(2026,11,15),self.now)

    def test_missing_reversed_future_or_naive_inputs_rejected(self):
        for a,b in [(date(2026,11,15),None),(None,date(2026,11,15)),
                    (date(2026,11,16),date(2026,11,15)),
                    (date(2026,11,16),date(2026,11,17))]:
            with self.subTest(a=a,b=b),self.assertRaises(ValueError):
                analytics_window(a,b,self.now)
        with self.assertRaises(ValueError):
            analytics_window(now=datetime(2026,11,16))


if __name__ == '__main__':
    unittest.main()
