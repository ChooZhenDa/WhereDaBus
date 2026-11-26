"""Published Singapore adult card fares for a single direct bus ride."""
from bisect import bisect_left
from datetime import date
from decimal import Decimal, InvalidOperation


PTC_FARES = 'https://www.ptc.gov.sg/fares/public-transport-fares-and-passes/'
PTC_REVIEW_2026 = 'https://www.ptc.gov.sg/media-centre/newsroom/fare-review-exercise-2026/'
START_2025 = date(2025, 12, 27)
START_2026 = date(2026, 12, 26)

# PTC's adult trunk card table. The first 38 entries end at 3.2, 4.2, ...
# 40.2 km inclusive. The final entry is the open-ended band above 40.2 km.
BAND_ENDS_KM = tuple(Decimal(tenths) / 10 for tenths in range(32, 403, 10))
TRUNK_CENTS_2025 = (
    128, 138, 149, 159, 168, 175, 182, 186, 190, 194, 198, 202, 207,
    211, 215, 220, 224, 227, 230, 233, 236, 238, 240, 242, 243, 244,
    245, 246, 247, 248, 249, 250, 251, 252, 253, 254, 255, 256, 257,
)
# Annex B of the 2026 fare review increases the first adult band by 12 cents
# and each remaining band by 13. The adult express differential stays S$1.
TRUNK_CENTS_2026 = tuple(cents + (12 if i == 0 else 13)
                         for i, cents in enumerate(TRUNK_CENTS_2025))


def adult_card_fare(route, travel_date):
    """Estimate the standalone fare before personal concessions or rebates."""
    approved_2026 = travel_date >= START_2026
    effective = START_2026 if approved_2026 else START_2025
    result = {'status': 'unavailable', 'cents': None, 'currency': 'SGD',
              'effectiveFrom': effective.isoformat(), 'reason': None,
              'sourceUrl': PTC_REVIEW_2026 if approved_2026 else PTC_FARES}
    if travel_date < START_2025:
        return {**result, 'effectiveFrom': None, 'reason': 'No fare table for this date.'}
    category = (route.get('category') or '').strip().upper()
    if category not in {'TRUNK', 'FEEDER', 'EXPRESS', 'CITY_LINK'}:
        return {**result, 'reason': 'Fare unavailable for this service category.'}
    fares = TRUNK_CENTS_2026 if approved_2026 else TRUNK_CENTS_2025
    if category == 'FEEDER':
        # PTC sets a single fare for a feeder ride regardless of its length.
        cents = fares[0]
    else:
        try:
            distance = Decimal(str(route.get('distance_km')))
        except InvalidOperation:
            distance = None
        if (route.get('segment_coverage') != 'no_detected_gaps' or distance is None
                or not distance.is_finite() or distance <= 0):
            return {**result, 'reason': 'Fare unavailable without a confirmed journey distance.'}
        cents = fares[bisect_left(BAND_ENDS_KM, distance)]
        # LTA's City Direct services use distance-based express fares; see
        # https://travelguide.simplygo.com.sg/Bus/Info/666 .
        if category in {'EXPRESS', 'CITY_LINK'}:
            cents += 100
    return {**result, 'status': 'available', 'cents': cents}
