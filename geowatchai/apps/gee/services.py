import logging
import os
import json
import time
from typing import Optional, Dict, Any, Tuple
from pathlib import Path
import ee
from django.contrib.gis.geos import Polygon, GEOSGeometry
from decouple import config
from apps.jobs.models import Job

logger = logging.getLogger(__name__)


class GeeService:
    """Google Earth Engine integration service following Anti-Vibe guardrails"""

    def __init__(self):
        self._ee_initialized = False
        self._authenticate()

    def _authenticate(self):
        """Initialize GEE authentication using service account credentials"""
        try:
            service_account_path = config('GEE_SERVICE_ACCOUNT', default='')
            project_id = config('GEE_PROJECT_ID', default='')

            if not service_account_path or not project_id:
                logger.warning("GEE credentials not configured, using mock mode")
                self._ee_initialized = False
                return

            if not os.path.exists(service_account_path):
                logger.error(f"GEE service account file not found: {service_account_path}")
                self._ee_initialized = False
                return

            # Initialize GEE with service account
            credentials = ee.ServiceAccountCredentials(service_account_path, project_id)
            ee.Initialize(credentials)

            self._ee_initialized = True
            logger.info("GEE authentication successful")

        except Exception as e:
            logger.error(f"GEE authentication failed: {str(e)}")
            self._ee_initialized = False

    def validate_aoi(self, aoi_geometry: Polygon) -> Tuple[bool, Optional[str]]:
        """
        Validate AOI geometry against constraints

        Args:
            aoi_geometry: PostGIS Polygon geometry

        Returns:
            Tuple[bool, Optional[str]]: (is_valid, error_message)
        """
        try:
            if not aoi_geometry.valid:
                return False, "Invalid geometry"

            # Calculate area in hectares
            area_sq_m = aoi_geometry.area
            area_hectares = area_sq_m / 10000

            max_area = config('MAX_AOI_AREA', default=1000000, cast=float)
            if area_hectares > max_area:
                return False, f"AOI area {area_hectares:.2f}ha exceeds maximum {max_area}ha"

            # Check geometry complexity (vertex count)
            vertex_count = len(aoi_geometry.coords[0])
            if vertex_count > 1000:
                return False, f"Geometry too complex: {vertex_count} vertices (max 1000)"

            return True, None

        except Exception as e:
            logger.error(f"AOI validation error: {str(e)}")
            return False, f"Validation error: {str(e)}"

    def simplify_geometry(self, aoi_geometry: Polygon, tolerance: float = 0.001) -> Polygon:
        """
        Simplify geometry to prevent GEE API timeouts

        Args:
            aoi_geometry: PostGIS Polygon geometry
            tolerance: Simplification tolerance in degrees

        Returns:
            Polygon: Simplified geometry
        """
        try:
            # Use PostGIS simplify function
            simplified = aoi_geometry.simplify(tolerance, preserve_topology=True)

            # Ensure result is still valid
            if not simplified.valid:
                logger.warning("Simplified geometry invalid, using original")
                return aoi_geometry

            vertex_reduction = len(aoi_geometry.coords[0]) - len(simplified.coords[0])
            logger.info(f"Geometry simplified: reduced {vertex_reduction} vertices")

            return simplified

        except Exception as e:
            logger.error(f"Geometry simplification error: {str(e)}")
            return aoi_geometry

    def geometry_to_ee(self, aoi_geometry: Polygon) -> Optional[ee.Geometry]:
        """
        Convert PostGIS Polygon to GEE Geometry

        Args:
            aoi_geometry: PostGIS Polygon geometry

        Returns:
            ee.Geometry: GEE geometry or None if conversion fails
        """
        try:
            if not self._ee_initialized:
                logger.warning("GEE not initialized, returning None")
                return None

            # Get coordinates in GeoJSON format
            geojson = json.loads(aoi_geometry.geojson)

            # Convert to GEE geometry
            ee_geometry = ee.Geometry(geojson)

            return ee_geometry

        except Exception as e:
            logger.error(f"Geometry conversion error: {str(e)}")
            return None

    def get_hls_collection(self, start_date: str, end_date: str) -> Optional[ee.ImageCollection]:
        """
        Get HLS collection filtered by date range

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format

        Returns:
            ee.ImageCollection: Filtered HLS collection or None
        """
        try:
            if not self._ee_initialized:
                logger.warning("GEE not initialized, returning None")
                return None

            collection_name = config('HLS_COLLECTION', default='COPERNICUS/S2_SR_HARMONIZED')

            # Get collection and filter by date
            collection = ee.ImageCollection(collection_name) \
                .filterDate(start_date, end_date)

            logger.info(f"HLS collection loaded: {collection.size().getInfo()} images")
            return collection

        except Exception as e:
            logger.error(f"HLS collection error: {str(e)}")
            return None

    def apply_cloud_mask(self, image: ee.Image) -> ee.Image:
        """
        Apply cloud masking to HLS image

        Args:
            image: GEE Image

        Returns:
            ee.Image: Cloud-masked image
        """
        try:
            cloud_mask_band = config('CLOUD_MASK_BAND', default='QA60')

            # Cloud mask for Sentinel-2
            cloud_bit_mask = (1 << 10)
            cirrus_bit_mask = (1 << 11)

            # Create cloud mask
            cloud_mask = image.select(cloud_mask_band).bitwiseAnd(cloud_bit_mask).eq(0)
            cirrus_mask = image.select(cloud_mask_band).bitwiseAnd(cirrus_bit_mask).eq(0)

            # Apply mask
            masked_image = image.updateMask(cloud_mask.And(cirrus_mask))

            return masked_image

        except Exception as e:
            logger.error(f"Cloud masking error: {str(e)}")
            return image

    def select_bands(self, image: ee.Image) -> ee.Image:
        """
        Select required bands for analysis (RGB + NIR)

        Args:
            image: GEE Image

        Returns:
            ee.Image: Image with selected bands
        """
        try:
            # All 6 bands required by the preprocessing service:
            # B2=Blue (for BSI), B3=Green, B4=Red, B8=NIR, B11=SWIR1, B12=SWIR2
            bands = ['B2', 'B3', 'B4', 'B8', 'B11', 'B12']
            selected = image.select(bands)
            return selected

        except Exception as e:
            logger.error(f"Band selection error: {str(e)}")
            return image

    def export_hls_imagery(self, job: Job) -> Dict[str, Any]:
        """
        Export HLS imagery for job AOI and date range

        Args:
            job: Job instance with AOI and date range

        Returns:
            Dict[str, Any]: Export result with task ID
        """
        try:
            if not self._ee_initialized:
                return {
                    'success': False,
                    'error': 'GEE not initialized',
                    'export_id': None
                }

            # Validate AOI
            is_valid, error_msg = self.validate_aoi(job.aoi_geometry)
            if not is_valid:
                return {
                    'success': False,
                    'error': error_msg,
                    'export_id': None
                }

            # Simplify geometry if needed
            simplified_aoi = self.simplify_geometry(job.aoi_geometry)

            # Convert to GEE geometry
            ee_geometry = self.geometry_to_ee(simplified_aoi)
            if ee_geometry is None:
                return {
                    'success': False,
                    'error': 'Failed to convert geometry',
                    'export_id': None
                }

            # Get HLS collection
            collection = self.get_hls_collection(
                job.start_date.strftime('%Y-%m-%d'),
                job.end_date.strftime('%Y-%m-%d')
            )
            if collection is None:
                return {
                    'success': False,
                    'error': 'Failed to load HLS collection',
                    'export_id': None
                }

            # Process collection: cloud mask, band selection, mosaic
            processed = collection \
                .map(self.apply_cloud_mask) \
                .map(self.select_bands) \
                .median()  # Create median composite

            # Clip to AOI
            clipped = processed.clip(ee_geometry)

            # Export configuration
            gcs_bucket = config('GCS_BUCKET', default='geo-vigil-guard-exports')
            export_params = {
                'image': clipped,
                'description': f'job_{job.id}_hls_export',
                'bucket': gcs_bucket,
                'fileNamePrefix': f'jobs/{job.id}/hls_imagery',
                'scale': config('MAX_RESOLUTION', default=30, cast=int),
                'fileFormat': 'GeoTIFF',
                'maxPixels': 1e10,
                'region': ee_geometry,
            }

            # Compute mean cloud cover for the composite period
            try:
                mean_cloud = float(collection.aggregate_mean('CLOUDY_PIXEL_PERCENTAGE').getInfo() or 0.0)
            except Exception:
                mean_cloud = 0.0

            # Determine satellite from collection name
            collection_name = config('HLS_COLLECTION', default='COPERNICUS/S2_SR_HARMONIZED')
            if 'LC08' in collection_name or 'LANDSAT_8' in collection_name or 'L8' in collection_name:
                satellite = 'L8'
            elif 'LC09' in collection_name or 'LANDSAT_9' in collection_name or 'L9' in collection_name:
                satellite = 'L9'
            else:
                satellite = 'S2A'

            # Deterministic scene ID from job + date range
            scene_id = (
                f"HLS.{satellite}.job_{job.id}"
                f".{job.start_date.strftime('%Y%m%d')}"
                f"_{job.end_date.strftime('%Y%m%d')}"
            )

            # Start export task
            task = ee.batch.Export.image.toCloudStorage(**export_params)
            task.start()

            logger.info(f"Started GEE export task: {task.id}")

            return {
                'success': True,
                'export_id': task.id,
                'task': task,
                'scene_id': scene_id,
                'satellite': satellite,
                'cloud_cover_pct': mean_cloud,
            }

        except Exception as e:
            logger.error(f"HLS export error: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'export_id': None
            }

    def monitor_export(self, export_id: str, timeout: int = None) -> Dict[str, Any]:
        """
        Monitor GEE export task status with exponential backoff

        Args:
            export_id: GEE task ID
            timeout: Maximum wait time in seconds

        Returns:
            Dict[str, Any]: Export status and result
        """
        try:
            if not self._ee_initialized:
                return {
                    'status': 'failed',
                    'error': 'GEE not initialized'
                }

            if timeout is None:
                timeout = config('EXPORT_TIMEOUT', default=3600, cast=int)

            start_time = time.time()
            wait_time = 5  # Initial wait time
            max_wait = 300  # Maximum wait time (5 minutes)

            while time.time() - start_time < timeout:
                # Get task status
                task = ee.batch.Task(export_id)
                status = task.status()

                state = status.get('state', 'UNKNOWN')

                if state == 'COMPLETED':
                    logger.info(f"Export {export_id} completed successfully")
                    return {
                        'status': 'completed',
                        'export_url': status.get('destination_uris', [None])[0]
                    }
                elif state == 'FAILED':
                    error_message = status.get('error_message', 'Unknown error')
                    logger.error(f"Export {export_id} failed: {error_message}")
                    return {
                        'status': 'failed',
                        'error': error_message
                    }
                elif state == 'CANCELLED':
                    logger.warning(f"Export {export_id} was cancelled")
                    return {
                        'status': 'cancelled',
                        'error': 'Export was cancelled'
                    }

                # Exponential backoff with jitter
                time.sleep(wait_time)
                wait_time = min(wait_time * 1.5, max_wait)

            # Timeout reached
            logger.warning(f"Export {export_id} timed out after {timeout}s")

            # Try to cancel the task
            try:
                task = ee.batch.Task(export_id)
                task.cancel()
            except:
                pass

            return {
                'status': 'timeout',
                'error': f'Export timed out after {timeout} seconds'
            }

        except Exception as e:
            logger.error(f"Export monitoring error: {str(e)}")
            return {
                'status': 'error',
                'error': str(e)
            }

    def get_service_info(self) -> Dict[str, Any]:
        """
        Get GEE service information for debugging

        Returns:
            Dict[str, Any]: Service status and configuration
        """
        return {
            'initialized': self._ee_initialized,
            'project_id': config('GEE_PROJECT_ID', default='Not configured'),
            'max_aoi_area': config('MAX_AOI_AREA', default=1000000),
            'max_resolution': config('MAX_RESOLUTION', default=30),
            'export_timeout': config('EXPORT_TIMEOUT', default=3600),
            'hls_collection': config('HLS_COLLECTION', default='COPERNICUS/S2_SR_HARMONIZED')
        }


# Global service instance
_gee_service = None


def get_gee_service() -> GeeService:
    """
    Get global GEE service instance

    Returns:
        GeeService: Global service instance
    """
    global _gee_service
    if _gee_service is None:
        _gee_service = GeeService()
    return _gee_service