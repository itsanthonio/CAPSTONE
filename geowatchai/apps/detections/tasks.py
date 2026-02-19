"""
Celery tasks for the detections app.
Currently handles async timelapse fetching from Google Earth Engine.
"""

import logging
from datetime import date
from celery import shared_task

logger = logging.getLogger(__name__)

# Years to fetch going back from detection year
TIMELAPSE_YEARS_BACK = 5


@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def fetch_site_timelapse(self, site_id: str) -> dict:
    """
    For a DetectedSite, fetch one annual RGB composite per year
    going back TIMELAPSE_YEARS_BACK years using GEE.

    Stores a SiteTimelapse record per year with thumbnail_url,
    mean_ndvi, and mean_bsi so the frontend can render a slider.

    Triggered automatically by the orchestrator immediately after
    a site is created — no confirmation required.
    """
    try:
        from apps.detections.models import DetectedSite, SiteTimelapse
        from apps.gee.services import get_gee_service
        import ee
        import json

        site = DetectedSite.objects.get(id=site_id)
        gee_service = get_gee_service()

        if not gee_service._ee_initialized:
            logger.warning(
                f"[Timelapse] GEE not initialized, skipping timelapse for site {site_id}"
            )
            return {'status': 'skipped', 'reason': 'GEE not initialized'}

        detection_year = site.detection_date.year
        start_year = detection_year - TIMELAPSE_YEARS_BACK

        # Site centroid for spatial queries
        centroid = site.geometry.centroid
        lon, lat = centroid.x, centroid.y

        # Buffer 500 m around centroid for context
        ee_point = ee.Geometry.Point([lon, lat])
        ee_region = ee_point.buffer(500).bounds()

        frames_created = 0

        for year in range(start_year, detection_year + 1):
            # Skip if already fetched
            if SiteTimelapse.objects.filter(detected_site=site, year=year).exists():
                continue

            try:
                # Use dry season (Dec–Feb) for best cloud-free imagery in Ghana
                start_date = f"{year}-11-01"
                end_date = f"{year + 1}-03-31"

                collection = (
                    ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                    .filterBounds(ee_region)
                    .filterDate(start_date, end_date)
                    .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))
                )

                count = collection.size().getInfo()
                if count == 0:
                    logger.info(
                        f"[Timelapse] No clear imagery for site {site_id} year {year}"
                    )
                    continue

                # Build RGB composite
                composite = collection.median()

                # True-colour RGB (B4=Red, B3=Green, B2=Blue)
                rgb = composite.select(['B4', 'B3', 'B2'])

                # Get thumbnail URL (256×256 PNG)
                thumb_url = rgb.getThumbURL({
                    'region': ee_region,
                    'dimensions': 256,
                    'format': 'png',
                    'min': 0,
                    'max': 3000,
                    'gamma': 1.4,
                })

                # Compute mean NDVI = (B8 - B4) / (B8 + B4)
                ndvi = composite.normalizedDifference(['B8', 'B4'])
                mean_ndvi = ndvi.reduceRegion(
                    reducer=ee.Reducer.mean(),
                    geometry=ee_region,
                    scale=30,
                    maxPixels=1e6
                ).get('nd').getInfo()

                # Compute mean BSI = ((B11+B4)-(B8+B2)) / ((B11+B4)+(B8+B2))
                b2 = composite.select('B2').divide(10000)
                b4 = composite.select('B4').divide(10000)
                b8 = composite.select('B8').divide(10000)
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

                # Cloud cover average for this composite
                mean_cloud = collection.aggregate_mean(
                    'CLOUDY_PIXEL_PERCENTAGE'
                ).getInfo()

                SiteTimelapse.objects.create(
                    detected_site=site,
                    year=year,
                    acquisition_period=str(year),
                    thumbnail_url=thumb_url or '',
                    cloud_cover_pct=mean_cloud,
                    mean_ndvi=mean_ndvi,
                    mean_bsi=mean_bsi,
                )
                frames_created += 1
                logger.info(f"[Timelapse] Created frame year {year} for site {site_id}")

            except Exception as year_exc:
                logger.warning(
                    f"[Timelapse] Failed year {year} for site {site_id}: {year_exc}"
                )
                continue

        logger.info(
            f"[Timelapse] Done for site {site_id} — "
            f"{frames_created} frames created"
        )
        return {'status': 'completed', 'site_id': site_id, 'frames': frames_created}

    except DetectedSite.DoesNotExist:
        logger.error(f"[Timelapse] Site {site_id} not found")
        return {'status': 'failed', 'error': 'Site not found'}

    except Exception as exc:
        logger.error(f"[Timelapse] Task failed for site {site_id}: {exc}", exc_info=True)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        return {'status': 'failed', 'site_id': site_id, 'error': str(exc)}
