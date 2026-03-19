"""
Celery tasks for the detections app.
Handles async timelapse fetching from Planet Labs (Education Program).
"""

import os
import logging
import requests
from celery import shared_task
from django.conf import settings
from decouple import config

logger = logging.getLogger(__name__)

TIMELAPSE_YEARS_BACK = 5
TIMELAPSE_MAX_FRAMES = 5

PLANET_API_URL = "https://api.planet.com/data/v1"


def _save_thumbnail(image_bytes: bytes, site_id: str, year: int) -> str:
    """Save image bytes to MEDIA_ROOT and return the relative URL."""
    rel_dir = os.path.join('timelapse', site_id)
    abs_dir = os.path.join(settings.MEDIA_ROOT, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)
    filename = f"{year}.png"
    abs_path = os.path.join(abs_dir, filename)
    with open(abs_path, 'wb') as f:
        f.write(image_bytes)
    return os.path.join(settings.MEDIA_URL, rel_dir, filename).replace('\\', '/')


def _planet_search(geometry_geojson: dict, start_date: str, end_date: str, api_key: str) -> list:
    """
    Search Planet's Data API for PSScene items within the AOI and date range.
    Returns list of items sorted by cloud cover ascending.
    """
    search_filter = {
        "type": "AndFilter",
        "config": [
            {
                "type": "GeometryFilter",
                "field_name": "geometry",
                "config": geometry_geojson
            },
            {
                "type": "DateRangeFilter",
                "field_name": "acquired",
                "config": {
                    "gte": f"{start_date}T00:00:00Z",
                    "lte": f"{end_date}T23:59:59Z"
                }
            },
            {
                "type": "RangeFilter",
                "field_name": "cloud_cover",
                "config": {"lte": 0.2}
            }
        ]
    }

    payload = {
        "item_types": ["PSScene"],
        "filter": search_filter
    }

    resp = requests.post(
        f"{PLANET_API_URL}/quick-search",
        json=payload,
        auth=(api_key, ""),
        timeout=30
    )
    resp.raise_for_status()

    features = resp.json().get("features", [])
    # Sort by cloud cover ascending so we pick the clearest scene
    features.sort(key=lambda f: f.get("properties", {}).get("cloud_cover", 1.0))
    return features


def _planet_thumbnail(item: dict, api_key: str, geometry_geojson: dict, width: int = 600) -> bytes:
    """
    Download the thumbnail for a Planet scene item, cropped to an 800m buffer
    around the detection site centroid so the mining site is always centred.
    """
    from shapely.geometry import shape

    thumb_url = item.get("_links", {}).get("thumbnail")
    if not thumb_url:
        raise ValueError(f"No thumbnail link for item {item.get('id')}")

    centroid = shape(geometry_geojson).centroid
    offset = 0.0072  # ~800m in degrees at Ghana's latitude (~7°N)
    bbox = f"{centroid.x - offset},{centroid.y - offset},{centroid.x + offset},{centroid.y + offset}"

    resp = requests.get(
        thumb_url,
        params={"width": width, "bbox": bbox},
        auth=(api_key, ""),
        timeout=60
    )
    resp.raise_for_status()
    return resp.content


@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def fetch_site_timelapse(self, site_id: str) -> dict:
    """
    For a DetectedSite, fetch one annual RGB composite per year
    for the last TIMELAPSE_YEARS_BACK years using Planet Labs imagery.

    Downloads thumbnails locally so the browser can load them without
    Planet authentication. Stores a SiteTimelapse record per year.
    """
    try:
        from apps.detections.models import DetectedSite, SiteTimelapse
        import json as _json

        api_key = config('PLANET_API_KEY', default='')
        if not api_key:
            logger.warning(f"[Timelapse] PLANET_API_KEY not set, skipping site {site_id}")
            return {'status': 'skipped', 'reason': 'PLANET_API_KEY not configured'}

        site = DetectedSite.objects.get(id=site_id)

        detection_year = site.detection_date.year
        search_start = detection_year - (TIMELAPSE_YEARS_BACK + 2)

        geometry_geojson = _json.loads(site.geometry.geojson)

        frames_created = 0
        years_with_data = []

        for year in range(detection_year, search_start - 1, -1):
            if len(years_with_data) >= TIMELAPSE_MAX_FRAMES:
                break

            existing = SiteTimelapse.objects.filter(detected_site=site, year=year).first()
            if existing and existing.thumbnail_url and existing.thumbnail_url.startswith('/'):
                years_with_data.append(year)
                continue

            try:
                # Dry season (Nov–Mar) = best cloud-free imagery in Ghana
                start_date = f"{year}-11-01"
                end_date   = f"{year + 1}-03-31"

                items = _planet_search(geometry_geojson, start_date, end_date, api_key)

                if not items:
                    logger.info(f"[Timelapse] No Planet imagery for site {site_id} year {year}")
                    continue

                # Use the clearest scene
                best_item = items[0]
                cloud_cover = best_item.get("properties", {}).get("cloud_cover", None)
                cloud_cover_pct = round(cloud_cover * 100, 1) if cloud_cover is not None else None

                image_bytes = _planet_thumbnail(best_item, api_key, geometry_geojson, width=600)
                local_url = _save_thumbnail(image_bytes, site_id, year)

                SiteTimelapse.objects.update_or_create(
                    detected_site=site,
                    year=year,
                    defaults=dict(
                        acquisition_period=str(year),
                        thumbnail_url=local_url,
                        cloud_cover_pct=cloud_cover_pct,
                        mean_ndvi=None,
                        mean_bsi=None,
                    ),
                )
                years_with_data.append(year)
                frames_created += 1
                logger.info(f"[Timelapse] Saved Planet frame year {year} for site {site_id} (cloud: {cloud_cover_pct}%)")

            except Exception as year_exc:
                logger.warning(f"[Timelapse] Failed year {year} for site {site_id}: {year_exc}")
                continue

        logger.info(f"[Timelapse] Done for site {site_id} — {frames_created} new frames")
        return {'status': 'completed', 'site_id': site_id, 'frames': frames_created}

    except DetectedSite.DoesNotExist:
        logger.error(f"[Timelapse] Site {site_id} not found")
        return {'status': 'failed', 'error': 'Site not found'}

    except Exception as exc:
        logger.error(f"[Timelapse] Task failed for site {site_id}: {exc}", exc_info=True)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        return {'status': 'failed', 'site_id': site_id, 'error': str(exc)}
