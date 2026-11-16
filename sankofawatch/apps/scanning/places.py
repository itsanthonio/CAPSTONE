"""Name the nearest town or village for a map position, so scan tiles can be
shown as "Near Tarkwa, Western" instead of an internal tile code."""
import math

from .models import GhanaPlace

# Settlements only. The table also holds schools, banks, fuel stations, etc.,
# which make poor area names.
SETTLEMENT_TYPES = ('city', 'town', 'suburb', 'village', 'hamlet', 'neighbourhood', 'locality')
SEARCH_RADIUS_DEG = 0.2   # about 22 km

_cache = {}


def nearest_place_label(lat, lng):
    """Return e.g. "Tarkwa, Western" for the closest settlement, or "" if none is near.

    Results are cached per ~1 km cell; empty results are not cached so a later
    places import is picked up without a restart.
    """
    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        return ''
    key = (round(lat, 2), round(lng, 2))
    if key in _cache:
        return _cache[key]

    candidates = GhanaPlace.objects.filter(
        feature_code__in=SETTLEMENT_TYPES,
        latitude__range=(lat - SEARCH_RADIUS_DEG, lat + SEARCH_RADIUS_DEG),
        longitude__range=(lng - SEARCH_RADIUS_DEG, lng + SEARCH_RADIUS_DEG),
    ).values_list('name', 'region', 'latitude', 'longitude')

    cos_lat = math.cos(math.radians(lat))
    best, best_d = None, None
    for name, region, plat, plng in candidates:
        d = (plat - lat) ** 2 + ((plng - lng) * cos_lat) ** 2
        if best_d is None or d < best_d:
            best, best_d = (name, region), d
    if best is None:
        return ''
    label = f'{best[0]}, {best[1]}' if best[1] else best[0]
    _cache[key] = label
    return label


def tile_place_label(tile):
    """Place label for a ScanTile (uses its centroid)."""
    try:
        c = tile.geometry.centroid
        return nearest_place_label(c.y, c.x)
    except Exception:
        return ''
