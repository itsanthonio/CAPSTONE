"""
Celery tasks for the detections app.
Currently handles async timelapse fetching from Google Earth Engine.
"""

import os
import logging
import requests
from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)

# How many years back to search for available imagery
TIMELAPSE_YEARS_BACK = 5
# Max frames to store (last N years that actually have data)
TIMELAPSE_MAX_FRAMES = 5


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


@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def fetch_site_timelapse(self, site_id: str) -> dict:
    """
    For a DetectedSite, fetch one annual RGB composite per year
    for the last TIMELAPSE_YEARS_BACK years that have available imagery.

    Downloads thumbnails locally so the browser can load them without
    GEE authentication. Stores a SiteTimelapse record per year.
    """
    try:
        from apps.detections.models import DetectedSite, SiteTimelapse
        from apps.gee.services import get_gee_service
        import ee

        site = DetectedSite.objects.get(id=site_id)
        gee_service = get_gee_service()

        if not gee_service._ee_initialized:
            logger.warning(f"[Timelapse] GEE not initialized, skipping site {site_id}")
            return {'status': 'skipped', 'reason': 'GEE not initialized'}

        detection_year = site.detection_date.year
        # Search from further back to find TIMELAPSE_MAX_FRAMES available years
        search_start = detection_year - (TIMELAPSE_YEARS_BACK + 2)

        centroid = site.geometry.centroid
        lon, lat = centroid.x, centroid.y

        ee_point = ee.Geometry.Point([lon, lat])
        ee_region = ee_point.buffer(1500).bounds()

        frames_created = 0
        years_with_data = []

        # Scan years newest-first to collect available years
        for year in range(detection_year, search_start - 1, -1):
            if len(years_with_data) >= TIMELAPSE_MAX_FRAMES:
                break
            existing = SiteTimelapse.objects.filter(detected_site=site, year=year).first()
            # Only skip if we already have a valid locally-served URL
            if existing and existing.thumbnail_url and existing.thumbnail_url.startswith('/'):
                years_with_data.append(year)
                continue

            try:
                # Dry season (Nov–Mar) = best cloud-free imagery in Ghana
                start_date = f"{year}-11-01"
                end_date   = f"{year + 1}-03-31"

                collection = (
                    ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                    .filterBounds(ee_region)
                    .filterDate(start_date, end_date)
                    .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))
                )

                count = collection.size().getInfo()
                if count == 0:
                    logger.info(f"[Timelapse] No imagery for site {site_id} year {year}")
                    continue

                composite = collection.median()
                rgb = composite.select(['B4', 'B3', 'B2'])

                # Get GEE thumbnail URL — only used server-side to download
                gee_url = rgb.getThumbURL({
                    'region': ee_region,
                    'dimensions': 800,
                    'format': 'png',
                    'min': 0,
                    'max': 3000,
                    'gamma': 1.4,
                })

                # Download the image bytes on the worker (which has GEE auth)
                resp = requests.get(gee_url, timeout=60)
                if resp.status_code != 200:
                    logger.warning(f"[Timelapse] Failed to download thumb year {year}: HTTP {resp.status_code}")
                    continue

                local_url = _save_thumbnail(resp.content, site_id, year)

                # Compute mean NDVI
                ndvi = composite.normalizedDifference(['B8', 'B4'])
                mean_ndvi = ndvi.reduceRegion(
                    reducer=ee.Reducer.mean(),
                    geometry=ee_region,
                    scale=30,
                    maxPixels=1e6
                ).get('nd').getInfo()

                # Compute mean BSI
                b2  = composite.select('B2').divide(10000)
                b4  = composite.select('B4').divide(10000)
                b8  = composite.select('B8').divide(10000)
                b11 = composite.select('B11').divide(10000)
                bsi_img = (b11.add(b4).subtract(b8.add(b2))).divide(
                    b11.add(b4).add(b8.add(b2))
                ).rename('bsi')
                mean_bsi = bsi_img.reduceRegion(
                    reducer=ee.Reducer.mean(),
                    geometry=ee_region,
                    scale=30,
                    maxPixels=1e6
                ).get('bsi').getInfo()

                mean_cloud = collection.aggregate_mean('CLOUDY_PIXEL_PERCENTAGE').getInfo()

                SiteTimelapse.objects.update_or_create(
                    detected_site=site,
                    year=year,
                    defaults=dict(
                        acquisition_period=str(year),
                        thumbnail_url=local_url,
                        cloud_cover_pct=mean_cloud,
                        mean_ndvi=mean_ndvi,
                        mean_bsi=mean_bsi,
                    ),
                )
                years_with_data.append(year)
                frames_created += 1
                logger.info(f"[Timelapse] Saved frame year {year} for site {site_id}")

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
