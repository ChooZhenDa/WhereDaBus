from datetime import date
from decimal import Decimal
from unittest import TestCase

from backend.fares import adult_card_fare


class AdultFareTests(TestCase):
    def quote(self, distance, category='TRUNK', travel_date=date(2026, 11, 22),
              segment='no_detected_gaps'):
        return adult_card_fare({'distance_km': distance, 'category': category,
                                'segment_coverage': segment}, travel_date)

    def test_trunk_band_edges_and_maximum(self):
        for distance, expected in [('0.1', 128), ('3.20', 128), ('3.3', 138),
                                   ('4.2', 138), ('4.3', 149), ('17.2', 215),
                                   ('17.3', 220), ('40.2', 256), ('40.3', 257), ('80', 257)]:
            with self.subTest(distance=distance):
                self.assertEqual(self.quote(Decimal(distance))['cents'], expected)

    def test_express_and_city_direct_use_express_tariff(self):
        for category in ('EXPRESS', 'CITY_LINK'):
            self.assertEqual(self.quote(3.2, category)['cents'], 228)
            self.assertEqual(self.quote(50, category)['cents'], 357)

    def test_feeder_is_flat_regardless_of_missing_distance(self):
        self.assertEqual(self.quote(8.2, 'FEEDER')['cents'], 128)
        self.assertEqual(self.quote(None, 'FEEDER', segment='partial')['cents'], 128)

    def test_partial_segment_unknown_category_and_invalid_distance(self):
        for distance in (None, -1, 0, float('nan'), float('inf')):
            self.assertIsNone(self.quote(distance)['cents'])
        self.assertEqual(self.quote(3.2, segment='partial')['status'], 'unavailable')
        self.assertIsNone(self.quote(2, 'FLAT FEE')['cents'])
        self.assertIsNone(self.quote(2, '')['cents'])

    def test_new_fares_start_on_the_published_effective_date(self):
        eve, start = date(2026, 12, 25), date(2026, 12, 26)
        for category, distance, old, new in [('TRUNK', 3.2, 128, 140), ('TRUNK', 3.3, 138, 151),
                                             ('FEEDER', 5, 128, 140), ('EXPRESS', 45, 357, 370)]:
            self.assertEqual(self.quote(distance, category, eve)['cents'], old)
            fare = self.quote(distance, category, start)
            self.assertEqual(fare['cents'], new)
            self.assertEqual(fare['effectiveFrom'], '2026-12-26')
        self.assertIsNone(self.quote(3, travel_date=date(2025, 12, 26))['cents'])
