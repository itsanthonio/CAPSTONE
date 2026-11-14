"""Legal/illegal judging: a site is legal only if >= 80% of its area is inside concessions."""
from types import SimpleNamespace

from django.contrib.gis.geos import Polygon

from apps.detections.legal import LEGAL_MIN_OVERLAP_PCT, judge_site


class FakeConcessions:
    """Stands in for the LegalConcession queryset (judge_site only calls .filter(...).first())."""
    def __init__(self, geoms):
        self.items = [SimpleNamespace(geometry=g, name=f'C{i}') for i, g in enumerate(geoms)]

    def filter(self, **kwargs):
        return self

    def __iter__(self):
        return iter(self.items)

    def first(self):
        return self.items[0] if self.items else None


def box(x0, y0, x1, y1):
    return Polygon(((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)), srid=4326)


def site(x0, y0, x1, y1):
    return SimpleNamespace(geometry=box(x0, y0, x1, y1))


def test_threshold_is_80_percent():
    assert LEGAL_MIN_OVERLAP_PCT == 80.0


def test_fully_inside_is_legal():
    legal, best, pct = judge_site(site(1, 1, 2, 2), FakeConcessions([box(0, 0, 10, 10)]))
    assert legal and best is not None and round(pct) == 100


def test_centre_inside_but_mostly_outside_is_not_legal():
    # site spans x 0..10 (area 10); concession covers only x 0..4 -> 40% inside.
    # The old centre-point rule would have called this legal if the centre fell inside.
    legal, best, pct = judge_site(site(0, 0, 10, 1), FakeConcessions([box(0, 0, 4, 1)]))
    assert not legal and best is not None and round(pct) == 40


def test_85_percent_inside_is_legal():
    legal, _, pct = judge_site(site(0, 0, 10, 1), FakeConcessions([box(0, 0, 8.5, 1)]))
    assert legal and round(pct) == 85


def test_no_overlap_is_not_legal():
    legal, best, pct = judge_site(site(0, 0, 1, 1), FakeConcessions([]))
    assert not legal and best is None and pct == 0.0


def test_two_concessions_are_combined():
    # 45% + 45% of the site, in two separate concessions -> 90% inside overall
    legal, _, pct = judge_site(site(0, 0, 10, 1), FakeConcessions([box(0, 0, 4.5, 1), box(5, 0, 9.5, 1)]))
    assert legal and round(pct) == 90
