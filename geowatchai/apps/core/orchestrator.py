"""
Detection Orchestrator — coordinates the full pipeline:

  Job → GEE export → wait for GCS file → preprocess → inference
      → postprocess → DetectedSite records → legal classification
      → Alert generation → timelapse fetch (async)

Every step updates job.status so the frontend can track progress.
"""

import logging
import time
import json
from typing import Dict, Any, List, Optional

import numpy as np
from django.contrib.gis.geos import Polygon, GEOSGeometry

from apps.jobs.models import Job
from apps.jobs.services import JobService
from apps.gee.services import get_gee_service
from apps.preprocessing.services import get_preprocessing_service
from apps.inference.services import get_inference_service
from apps.postprocessing.services import get_postprocessor
from apps.results.models import Result

logger = logging.getLogger(__name__)


def _get_detection_models():
    from apps.detections.models import (
        DetectedSite, SatelliteImagery, ModelRun,
        LegalConcession, Alert, SiteTimelapse, Region,
    )
    return DetectedSite, SatelliteImagery, ModelRun, LegalConcession, Alert, SiteTimelapse, Region


BANDS_USED = ['B3', 'B4', 'B8', 'B11', 'B12', 'BSI']
MODEL_NAME = 'FPN-ResNet50-6band'
GEE_POLL_INTERVAL_SECONDS = 30
GEE_EXPORT_TIMEOUT_SECONDS = 3600


class MiningDetectionPipeline:
    """
    Orchestrates the complete illegal mining detection pipeline.

    Steps:
        1.  Validate job
        2.  Trigger GEE HLS export
        3.  Poll until GCS file is ready
        4.  Download GeoTIFF
        5.  Preprocess into 6-band tensor
        6.  Run FPN-ResNet50 inference
        7.  Post-process probability mask to polygons
        8.  Save raw Result blob (backward compat)
        9.  Explode Result features to DetectedSite records
        10. Spatial join against LegalConcession to set legal_status
        11. Generate Alert for each illegal site
        12. Enqueue timelapse fetch task for every new site
        13. Mark job COMPLETED
    """

    def __init__(self, threshold: float = 0.5, min_area_m2: float = 100.0):
        self.threshold = threshold
        self.min_area_m2 = min_area_m2
        self.gee_service = get_gee_service()
        self.preprocessing_service = get_preprocessing_service()
        self.inference_service = get_inference_service()
        self.postprocessor = get_postprocessor(threshold, min_area_m2)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def process_job(self, job_id: str) -> Dict[str, Any]:
        try:
            logger.info(f"[Pipeline] Starting job {job_id}")

            job = self._validate_job(job_id)
            geotiff_path, scene_id, satellite, cloud_cover = self._export_and_wait(job)
            tensor, raster_meta = self._preprocess(job, geotiff_path)
            probability_mask = self._infer(job, tensor)
            result, polygons = self._postprocess(job, probability_mask, raster_meta, geotiff_path)
            satellite_imagery = self._log_satellite_imagery(
                scene_id, satellite, cloud_cover, job, geotiff_path, raster_meta
            )
            model_run = self._log_model_run(job, satellite_imagery)
            detected_sites = self._create_detected_sites(job, result, model_run, satellite_imagery)
            # Save images AFTER sites exist so we can generate per-site crops
            self._save_patch_images(job, tensor, probability_mask, detected_sites, raster_meta)
            self._auto_promote_tile(job, detected_sites)
            self._assign_regions(detected_sites)
            self._classify_legal_status(detected_sites)
            self._generate_alerts(detected_sites)
            self._enqueue_timelapse_fetches(detected_sites)

            # Save detection results BEFORE marking completed (avoids race condition)
            job.total_detections = len(detected_sites)
            job.illegal_count = sum(1 for s in detected_sites if s.legal_status == 'illegal')
            job.result_id = result.id
            job.detection_data = {
                'type': 'FeatureCollection',
                'features': [
                    {
                        'type': 'Feature',
                        'geometry': json.loads(s.geometry.json),
                        'properties': {
                            'site_id': str(s.id),
                            'confidence_score': s.confidence_score,
                            'area_hectares': s.area_hectares,
                            'legal_status': s.legal_status,
                        }
                    } for s in detected_sites
                ]
            }
            job.save(update_fields=['total_detections', 'illegal_count', 'result_id', 'detection_data'])

            # Mark completed AFTER data is saved
            JobService.update_job_status(
                job_id=str(job.id),
                new_status=Job.Status.COMPLETED
            )
            logger.info(f"[Pipeline] Completed job {job_id} — {len(detected_sites)} sites")

            try:
                from apps.detections.models import AuditLog
                AuditLog.objects.create(
                    user=None,
                    action='job.completed',
                    object_id=str(job.id),
                    detail={
                        'source': job.source,
                        'total_detections': len(detected_sites),
                        'illegal_count': job.illegal_count or 0,
                    },
                )
            except Exception:
                pass

            # Immediate email only for manual scans; automated scans are
            # batched into the daily digest at 18:00 by automated_scan_daily_digest.
            if job.source != 'automated':
                try:
                    from apps.notifications.services import send_scan_completed
                    send_scan_completed(job)
                except Exception:
                    pass

            return {
                'status': 'completed',
                'job_id': job_id,
                'result_id': str(result.id),
                'total_detections': len(detected_sites),
                'illegal_count': sum(1 for s in detected_sites if s.legal_status == 'illegal'),
            }

        except Exception as exc:
            logger.error(f"[Pipeline] Job {job_id} failed: {exc}", exc_info=True)
            try:
                JobService.update_job_status(job_id, Job.Status.FAILED, failure_reason=str(exc))
            except Exception:
                pass
            try:
                from apps.jobs.models import Job as _Job
                from apps.detections.models import AuditLog
                from apps.notifications.services import send_scan_failed
                _job = _Job.objects.filter(id=job_id).first()
                if _job:
                    AuditLog.objects.create(
                        user=None,
                        action='job.failed',
                        object_id=str(_job.id),
                        detail={
                            'source': _job.source,
                            'reason': str(exc),
                        },
                    )
                    if _job.source != 'automated':
                        send_scan_failed(_job)
            except Exception:
                pass
            return {'status': 'failed', 'job_id': job_id, 'error': str(exc)}

    # ------------------------------------------------------------------
    # Step 1 — Validate
    # ------------------------------------------------------------------

    def _validate_job(self, job_id: str) -> Job:
        job = Job.objects.get(id=job_id)
        # Only accept QUEUED — if the job is already past this point it means
        # the pipeline is already running (e.g. a retried Celery task).
        if job.status != Job.Status.QUEUED:
            raise ValueError(
                f"Job {job_id} already processing (status='{job.status}'). Aborting duplicate run."
            )
        JobService.update_job_status(job_id, Job.Status.VALIDATING)
        return job

    # ------------------------------------------------------------------
    # Step 2 — GEE export + poll until GCS file ready
    # ------------------------------------------------------------------

    def _export_and_wait(self, job: Job):
        """
        Triggers GEE export using Direct Download and returns immediately.
        Returns (geotiff_path, scene_id, satellite, cloud_cover_pct).
        """
        # 1. Initialize EVERYTHING at the very top, before any 'try'
        export_result = {"status": "starting", "error": "Unknown initialization error"}
        error_msg = "Unknown error"
        
        JobService.update_job_status(str(job.id), Job.Status.EXPORTING)

        try:
            # 2. Ensure that assignment happens clearly
            export_result = self.gee_service.export_hls_imagery(job)
            logger.info(f"[Pipeline] GEE export result: {export_result}")
            
        except Exception as e:
            # 3. Use 'e' directly if export_result is still at default
            actual_error = export_result.get('error') if export_result.get('status') != 'starting' else str(e)
            logger.error(f"HLS export error: {actual_error}")
            # 4. This line below is where your current crash is happening:
            raise RuntimeError(f"GEE export failed: {actual_error}")
        
        # 5. Check if GEE returned an error immediately
        if not export_result or not export_result.get('success'):
            error_msg = export_result.get('error', 'GEE returned no data')
            raise RuntimeError(f"GEE export failed: {error_msg}")

        # 6. Direct download is immediate - no monitoring needed
        if export_result.get('is_local'):
            # Local export - file is already downloaded
            local_path = export_result.get('local_path')
            scene_id = export_result.get('scene_id', f"job_{job.id}")
            satellite = export_result.get('satellite', 'S2A')
            cloud_cover = export_result.get('cloud_cover_pct', 0.0)
            logger.info(f"[Pipeline] Direct download complete — {local_path}")
            return local_path, scene_id, satellite, cloud_cover
        else:
            # This should not happen with direct download, but handle for completeness
            raise RuntimeError(f"Unexpected non-local export result: {export_result}")

    # ------------------------------------------------------------------
    # Step 3 — Preprocess
    # ------------------------------------------------------------------

    def _preprocess(self, job: Job, geotiff_path: str):
        JobService.update_job_status(str(job.id), Job.Status.PREPROCESSING)
        local_path = self._resolve_local_path(geotiff_path, job)
        tensor, metadata = self.preprocessing_service.preprocess_geotiff(local_path)
        self.preprocessing_service.validate_tensor(tensor)
        logger.info(
            f"[Pipeline] Tensor {tensor.shape} "
            f"range [{tensor.min():.3f}, {tensor.max():.3f}]"
        )
        return tensor, metadata

    def _resolve_local_path(self, gcs_path: str, job: Job) -> str:
        if gcs_path.startswith('gs://'):
            from google.cloud import storage as gcs
            import tempfile, os

            # Parse gs://bucket/path/to/file.tif
            without_scheme = gcs_path[5:]  # strip "gs://"
            bucket_name, blob_path = without_scheme.split('/', 1)

            local_path = os.path.join(tempfile.gettempdir(), f"hls_{job.id}.tif")

            logger.info(f"[Pipeline] Downloading {gcs_path} -> {local_path}")
            client = gcs.Client()
            bucket = client.bucket(bucket_name)
            blob   = bucket.blob(blob_path)
            blob.download_to_filename(local_path)
            logger.info(f"[Pipeline] Download complete")
            return local_path
        return gcs_path

    # ------------------------------------------------------------------
    # Step 4 — Inference
    # ------------------------------------------------------------------

    def _infer(self, job: Job, tensor: np.ndarray) -> np.ndarray:
        JobService.update_job_status(str(job.id), Job.Status.INFERRING)
        probability_mask = self.inference_service.predict_tiled(tensor)
        logger.info(
            f"[Pipeline] Probability mask "
            f"[{probability_mask.min():.3f}, {probability_mask.max():.3f}]"
        )
        return probability_mask

    # ------------------------------------------------------------------
    # Step 5 — Postprocess → Result
    # ------------------------------------------------------------------

    def _postprocess(self, job: Job, probability_mask: np.ndarray,
                     metadata: dict, geotiff_path: str):
        JobService.update_job_status(str(job.id), Job.Status.POSTPROCESSING)

        from rasterio.transform import Affine
        transform = metadata.get('transform', Affine(1, 0, 0, 0, -1, 0))
        source_crs = metadata.get('crs')

        result = self.postprocessor.process_probability_mask(
            probability_mask,
            transform,
            job,
            model_version=job.model_version,
            tile_reference=geotiff_path,
            source_crs=source_crs,
        )
        polygons = result.geojson.get('features', [])
        logger.info(f"[Pipeline] Postprocessing yielded {len(polygons)} polygons")
        return result, polygons

    # ------------------------------------------------------------------
    # Step 5b — Save patch visualization images (non-blocking)
    # ------------------------------------------------------------------

    def _save_patch_images(self, job: Job, tensor: np.ndarray,
                           probability_mask: np.ndarray,
                           detected_sites=None, raster_meta=None):
        """Generate and persist ML visualization PNGs (whole-AOI + per-site). Never raises."""
        try:
            from apps.postprocessing.services import save_patch_images
            save_patch_images(job, tensor, probability_mask, detected_sites, raster_meta)
        except Exception as exc:
            logger.warning(f"[Pipeline] Patch image generation skipped: {exc}")

    # ------------------------------------------------------------------
    # Step 6 — Log SatelliteImagery
    # ------------------------------------------------------------------

    def _log_satellite_imagery(self, scene_id: str, satellite: str,
                                cloud_cover: float, job: Job,
                                gcs_path: str, metadata: dict):
        _, SatelliteImagery, *_ = _get_detection_models()
        imagery, _ = SatelliteImagery.objects.get_or_create(
            scene_id=scene_id,
            defaults={
                'satellite': satellite,
                'acquisition_date': job.end_date,
                'cloud_cover_pct': cloud_cover,
                'bands_processed': BANDS_USED,
                'preprocessing_version': job.preprocessing_version,
                'coverage_geometry': job.aoi_geometry,
                'gcs_path': gcs_path,
            }
        )
        return imagery

    # ------------------------------------------------------------------
    # Step 7 — Log ModelRun
    # ------------------------------------------------------------------

    def _log_model_run(self, job: Job, satellite_imagery):
        import os
        _, _, ModelRun, *_ = _get_detection_models()
        checkpoint_path = os.getenv('MODEL_PATH', 'unknown')
        return ModelRun.objects.create(
            job=job,
            model_name=MODEL_NAME,
            model_version=job.model_version,
            checkpoint_path=checkpoint_path,
            architecture='FPN',
            encoder='resnet50',
            bands_used=BANDS_USED,
            inference_threshold=self.threshold,
        )

    # ------------------------------------------------------------------
    # Step 8 — Explode Result → DetectedSite records
    # ------------------------------------------------------------------

    def _create_detected_sites(self, job: Job, result: Result,
                                model_run, satellite_imagery) -> List:
        DetectedSite, *_ = _get_detection_models()
        JobService.update_job_status(str(job.id), Job.Status.STORING)

        sites = []
        for feature in result.geojson.get('features', []):
            geom_dict = feature['geometry']
            props = feature['properties']

            geom = GEOSGeometry(json.dumps(geom_dict), srid=4326)
            if not isinstance(geom, Polygon):
                logger.warning(f"[Pipeline] Skipping non-polygon: {geom.geom_type}")
                continue

            # Clip detection polygon to the job AOI.
            # The downloaded TIF is a rectangle (bounding box of the AOI), so
            # detections near the edge can spill outside the drawn boundary.
            # Intersecting with the AOI ensures every stored polygon is fully
            # contained within what the user actually drew.
            try:
                clipped = geom.intersection(job.aoi_geometry)
                if clipped.empty:
                    logger.info("[Pipeline] Detection entirely outside AOI after clipping — skipped")
                    continue
                # intersection may return a MultiPolygon; keep only the largest part
                if clipped.geom_type == 'MultiPolygon':
                    clipped = max(clipped, key=lambda p: p.area)
                if not isinstance(clipped, Polygon) or clipped.empty:
                    continue
                geom = clipped
            except Exception as clip_err:
                logger.warning(f"[Pipeline] AOI clip failed, using unclipped geometry: {clip_err}")

            # Recalculate area from the clipped geometry (metric projection)
            try:
                geom_metric = geom.transform(32630, clone=True)  # UTM 30N — accurate for Ghana
                area_ha = geom_metric.area / 10_000.0
            except Exception:
                area_ha = props.get('area', 0.0) / 10_000.0

            # Deduplication: if an existing site centroid is within 500 m,
            # increment its recurrence counter instead of creating a duplicate.
            # SRID=4326 uses degrees — D(m=...) is rejected on geographic fields.
            # 0.0045° ≈ 500 m at Ghana's latitude (~7°N).
            existing = DetectedSite.objects.filter(
                centroid__dwithin=(geom.centroid, 0.0045)
            ).exclude(job=job).order_by('-detection_date').first()

            if existing:
                existing.recurrence_count += 1
                existing.detection_date = job.end_date
                existing.save(update_fields=['recurrence_count', 'detection_date', 'updated_at'])
                logger.info(f"[Pipeline] Dedup: merged new detection into existing site {existing.id}")
                # Save a snapshot for this recurrence
                from apps.detections.models import DetectionSnapshot
                DetectionSnapshot.objects.create(
                    site=existing,
                    job=job,
                    occurrence_number=existing.recurrence_count,
                    detection_date=job.end_date,
                    confidence_score=props.get('confidence_score', 0.0),
                    area_hectares=area_ha,
                )
                sites.append(existing)
                continue

            # Use the max-probability pixel as the centroid — more accurate than
            # the geometric centroid for pointing to the actual mining core.
            hotspot_lon = props.get('hotspot_lon')
            hotspot_lat = props.get('hotspot_lat')
            from django.contrib.gis.geos import Point as GEOSPoint
            hotspot = GEOSPoint(hotspot_lon, hotspot_lat, srid=4326) if (hotspot_lon is not None and hotspot_lat is not None) else None

            site = DetectedSite.objects.create(
                geometry=geom,
                centroid=hotspot,
                confidence_score=props.get('confidence_score', 0.0),
                area_hectares=area_ha,
                detection_date=job.end_date,
                job=job,
                model_run=model_run,
                satellite_imagery=satellite_imagery,
                first_detected_at=job.end_date,
            )
            # Save snapshot #1 for the initial detection
            from apps.detections.models import DetectionSnapshot
            DetectionSnapshot.objects.create(
                site=site,
                job=job,
                occurrence_number=1,
                detection_date=job.end_date,
                confidence_score=props.get('confidence_score', 0.0),
                area_hectares=area_ha,
            )
            sites.append(site)

        logger.info(f"[Pipeline] Created {len(sites)} DetectedSite records")
        return sites

    # ------------------------------------------------------------------
    # Step 9a — Assign Region via spatial join
    # ------------------------------------------------------------------

    def _assign_regions(self, sites: List) -> None:
        if not sites:
            return
        _, _, _, _, _, _, Region = _get_detection_models()
        active_regions = Region.objects.filter(is_active=True)
        for site in sites:
            region = active_regions.filter(
                geometry__contains=site.geometry.centroid
            ).first()
            if not region:
                region = active_regions.filter(
                    geometry__intersects=site.geometry
                ).first()
            if region:
                site.region = region
                site.save(update_fields=['region'])
        logger.info(f"[Pipeline] Region assignment done for {len(sites)} sites")

    # ------------------------------------------------------------------
    # Step 9 — Legal classification via spatial join
    # ------------------------------------------------------------------

    def _classify_legal_status(self, sites: List) -> None:
        if not sites:
            return

        DetectedSite, _, _, LegalConcession, *_ = _get_detection_models()
        active_concessions = LegalConcession.objects.filter(is_active=True)

        for site in sites:
            centroid = site.geometry.centroid

            # Primary check: is the centroid of the detected site inside a concession?
            # This is robust against raster-to-vector edge misalignment where polygon
            # boundaries may slightly cross the concession boundary.
            concession = active_concessions.filter(
                geometry__contains=centroid
            ).first()

            # Fallback: centroid on boundary edge — try any intersection
            if not concession:
                concession = active_concessions.filter(
                    geometry__intersects=site.geometry
                ).first()

            if concession:
                try:
                    intersection = site.geometry.intersection(concession.geometry)
                    overlap_pct = (
                        (intersection.area / site.geometry.area) * 100
                        if site.geometry.area > 0 else 0.0
                    )
                except Exception:
                    overlap_pct = 100.0  # centroid confirmed inside, assume full overlap

                site.intersecting_concession = concession
                site.concession_overlap_pct = overlap_pct
                site.legal_status = DetectedSite.LegalStatus.LEGAL
            else:
                site.legal_status = DetectedSite.LegalStatus.ILLEGAL

            site.save(update_fields=[
                'legal_status', 'intersecting_concession', 'concession_overlap_pct'
            ])

        illegal = sum(1 for s in sites if s.legal_status == 'illegal')
        logger.info(f"[Pipeline] Classification done — {illegal}/{len(sites)} illegal")

    # ------------------------------------------------------------------
    # Step 10 — Generate Alerts for illegal sites
    # ------------------------------------------------------------------

    def _generate_alerts(self, sites: List) -> None:
        if not sites:
            return

        DetectedSite, _, _, _, Alert, *_ = _get_detection_models()

        # Statuses that mean "an inspector is already handling this site"
        active_statuses = [
            Alert.AlertStatus.OPEN,
            Alert.AlertStatus.ACKNOWLEDGED,
            Alert.AlertStatus.DISPATCHED,
        ]

        created = 0
        for site in sites:
            if site.legal_status != DetectedSite.LegalStatus.ILLEGAL:
                continue

            # Skip if an active alert already exists — avoids alert spam on
            # every dedup scan of a known site.
            if Alert.objects.filter(detected_site=site, status__in=active_statuses).exists():
                continue

            if site.recurrence_count > 1:
                alert_type = Alert.AlertType.RECURRING_SITE
                severity = Alert.Severity.CRITICAL
            elif site.confidence_score >= 0.85:
                alert_type = Alert.AlertType.HIGH_CONFIDENCE
                severity = Alert.Severity.HIGH
            elif site.area_hectares > 5.0:
                alert_type = Alert.AlertType.EXPANSION_DETECTED
                severity = Alert.Severity.HIGH
            else:
                alert_type = Alert.AlertType.NEW_DETECTION
                severity = Alert.Severity.MEDIUM

            Alert.objects.create(
                detected_site=site,
                alert_type=alert_type,
                severity=severity,
                title=(
                    f"Illegal mining detected — "
                    f"{site.area_hectares:.2f} ha "
                    f"({site.confidence_score:.0%} confidence)"
                ),
                description=(
                    f"Detection date: {site.detection_date}\n"
                    f"Area: {site.area_hectares:.2f} ha\n"
                    f"Confidence: {site.confidence_score:.2%}\n"
                    f"Recurrence: {site.recurrence_count}x"
                ),
            )
            created += 1

        logger.info(f"[Pipeline] Generated {created} new alerts")

    # ------------------------------------------------------------------
    # Step 10b — Auto-promote tile to hotspot when mining is found
    # ------------------------------------------------------------------

    def _auto_promote_tile(self, job: Job, sites: List) -> None:
        """
        If this was an automated scan of a NORMAL tile and detections were found,
        promote the tile to HOTSPOT so it gets checked daily instead of weekly.
        """
        if not sites or not job.scan_tile_id:
            return
        try:
            from apps.scanning.models import ScanTile
            tile = job.scan_tile
            if tile.priority == ScanTile.Priority.NORMAL:
                tile.priority = ScanTile.Priority.HOTSPOT
                tile.save(update_fields=['priority'])
                logger.info(
                    f"[Pipeline] Auto-promoted tile '{tile.name}' to HOTSPOT "
                    f"({len(sites)} detection(s) found)"
                )
        except Exception as exc:
            logger.warning(f"[Pipeline] Auto-promote failed silently: {exc}")

    # ------------------------------------------------------------------
    # Step 11 — Enqueue timelapse fetches
    # ------------------------------------------------------------------

    def _enqueue_timelapse_fetches(self, sites: List) -> None:
        if not sites:
            return
        try:
            from apps.detections.tasks import fetch_site_timelapse
            for site in sites:
                fetch_site_timelapse.delay(str(site.id))
            logger.info(
                f"[Pipeline] Enqueued timelapse fetch for {len(sites)} sites"
            )
        except Exception as exc:
            logger.warning(f"[Pipeline] Could not enqueue timelapse tasks: {exc}")


# ---------------------------------------------------------------------------
# Module-level helpers used by tasks.py
# ---------------------------------------------------------------------------

_pipeline: Optional[MiningDetectionPipeline] = None


def get_detection_pipeline(threshold: float = 0.5,
                            min_area: float = 100.0) -> MiningDetectionPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = MiningDetectionPipeline(threshold, min_area)
    return _pipeline


def process_detection_job(job_id: str, threshold: float = 0.5,
                           min_area: float = 100.0) -> Dict[str, Any]:
    return get_detection_pipeline(threshold, min_area).process_job(job_id)


trigger_detection_pipeline = process_detection_job