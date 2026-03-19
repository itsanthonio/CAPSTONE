import logging
import os
import json
import time
import requests
from typing import Optional, Dict, Any, Tuple
from pathlib import Path
import ee
from django.contrib.gis.geos import Polygon, GEOSGeometry
from decouple import config
from apps.jobs.models import Job

logger = logging.getLogger(__name__)


def _to_gcs_uri(uri: str, meta: dict) -> str:
    """
    Convert a GEE destinationUri to a proper gs:// path.
    GEE metadata may not contain fileNamePrefix directly, so we parse the URI.
    """
    if not uri:
        return ''

    # Already a gs:// path — return as-is
    if uri.startswith('gs://'):
        return uri

    # Browser console URL formats:
    # https://console.developers.google.com/storage/browser/BUCKET/FOLDER/
    # https://console.cloud.google.com/storage/browser/BUCKET/FOLDER/
    for browser_prefix in [
        'https://console.developers.google.com/storage/browser/',
        'https://console.cloud.google.com/storage/browser/',
    ]:
        if uri.startswith(browser_prefix):
            # Strip the prefix and trailing slash to get "bucket/folder"
            path = uri[len(browser_prefix):].rstrip('/')
            # GEE always exports fileNamePrefix + ".tif"
            # The URI folder path IS the fileNamePrefix (without .tif)
            # e.g. "geo-vigil-guard-exports/jobs/UUID/hls_imagery"
            # But sometimes it's just the folder "geo-vigil-guard-exports/jobs/UUID/"
            # In that case append the default filename
            if not path.endswith('.tif'):
                # Check if it looks like a folder (ends with job UUID)
                # Append the known filename from export_params
                path = path + '/hls_imagery'
            return f"gs://{path}.tif"

    return uri


class GeeService:
    """Google Earth Engine integration service following Anti-Vibe guardrails"""

    def __init__(self):
        self._ee_initialized = False
        self._authenticate()

    def _authenticate(self):
        """Initialize GEE — service account if configured, else personal credentials"""
        try:
            service_account_path = config('GEE_SERVICE_ACCOUNT', default='')
            project_id = config('GEE_PROJECT_ID', default='')

            if service_account_path and os.path.exists(service_account_path) and project_id:
                # Production: service account
                credentials = ee.ServiceAccountCredentials(service_account_path, project_id)
                ee.Initialize(credentials)
                logger.info("GEE authenticated via service account")
            elif project_id:
                # Development: personal credentials (earthengine authenticate)
                ee.Initialize(project=project_id)
                logger.info(f"GEE authenticated via personal credentials (project={project_id})")
            else:
                logger.warning("GEE_PROJECT_ID not set — falling back to mock mode")
                self._ee_initialized = False
                return

            self._ee_initialized = True

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
        """Export HLS imagery for job AOI and date range using Direct Download"""
        # 1. INITIALIZE variables at the start to prevent NameErrors
        export_result = {'success': False, 'error': 'Unknown error', 'export_id': None}
        
        try:
            if not self._ee_initialized:
                return {'success': False, 'error': 'GEE not initialized', 'export_id': None}

            # Validate AOI
            is_valid, error_msg = self.validate_aoi(job.aoi_geometry)
            if not is_valid:
                return {'success': False, 'error': error_msg, 'export_id': None}

            # Simplify geometry if needed
            simplified_aoi = self.simplify_geometry(job.aoi_geometry)

            # Convert to GEE geometry
            ee_geometry = self.geometry_to_ee(simplified_aoi)
            if ee_geometry is None:
                return {'success': False, 'error': 'Failed to convert geometry', 'export_id': None}

            # Get HLS collection
            collection = self.get_hls_collection(
                job.start_date.strftime('%Y-%m-%d'),
                job.end_date.strftime('%Y-%m-%d')
            )
            if collection is None:
                return {'success': False, 'error': 'Failed to load HLS collection', 'export_id': None}

            # Process collection: cloud mask, band selection, mosaic
            processed = collection \
                .map(self.apply_cloud_mask) \
                .map(self.select_bands) \
                .median()  # Create median composite

            # Clip to AOI
            clipped = processed.clip(ee_geometry)

            # Define Metadata for return
            try:
                mean_cloud = float(collection.aggregate_mean('CLOUDY_PIXEL_PERCENTAGE').getInfo() or 0.0)
            except:
                mean_cloud = 0.0
            
            satellite = 'S2A' # Default
            scene_id = f"HLS.job_{job.id}.{job.start_date.strftime('%Y%m%d')}"

            # 2. DIRECT DOWNLOAD METHOD - bypass GCS/Billing
            local_path = f'local_exports/jobs/{job.id}/hls_imagery.tif'
            
            # Ensure directory exists
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            
            # 1. Get Download ID first
            try:
                # Use getDownloadId instead of getDownloadURL
                download_id = ee.data.getDownloadId({
                    'image': clipped,
                    'scale': config('MAX_RESOLUTION', default=30, cast=int),
                    'crs': 'EPSG:4326',
                    'filePerBand': False,
                    'name': 'hls_imagery',
                    'format': 'GEO_TIFF'
                })
                
                # 2. Construct the URL manually
                base_url = 'https://earthengine.googleapis.com/v1alpha/projects/earthengine-legacy/thumbnails'
                # Wait, legacy endpoint is more reliable for direct downloads:
                download_url = ee.data.makeDownloadUrl(download_id)
                
                logger.info(f"Generated download URL for job {job.id}")
                
                # Use requests to pull the file to your local_exports folder
                response = requests.get(download_url, timeout=300)  # 5 minute timeout
                if response.status_code == 200:
                    with open(local_path, 'wb') as f:
                        f.write(response.content)
                    logger.info(f"Direct download successful: {local_path}")
                    
                    return {
                        'success': True,
                        'export_id': f'direct_download_{job.id}',
                        'scene_id': scene_id,
                        'satellite': satellite,
                        'cloud_cover_pct': mean_cloud,
                        'is_local': True,
                        'local_path': local_path
                    }
                else:
                    raise Exception(f"GEE Download Failed: {response.text}")
                    
            except Exception as e:
                logger.error(f"Download trigger error: {str(e)}")
                error_msg = str(e)
                if 'too large' in error_msg.lower() or 'limit' in error_msg.lower():
                    error_msg = 'AOI too large for direct download. Please select a smaller area.'
                
                return {
                    'success': False,
                    'error': error_msg,
                    'export_id': None
                }

        except Exception as e:
            logger.error(f"HLS export error: {str(e)}")
            return {'success': False, 'error': str(e), 'export_id': None}

    def monitor_export(self, export_id: str, timeout: int = None) -> Dict[str, Any]:
        """
        Check GEE export task status ONCE and return immediately.
        The orchestrator loop handles waiting between calls.
        """
        try:
            if not self._ee_initialized:
                return {'status': 'failed', 'error': 'GEE not initialized'}

            # Handle local exports
            if export_id.startswith('local_export_'):
                # Local exports are always "completed" immediately
                return {'status': 'completed', 'export_url': f'local_file_{export_id}'}

            project_id = config('GEE_PROJECT_ID', default='')

            # Build full operation name if only short ID passed
            if '/' not in export_id:
                op_name = f'projects/{project_id}/operations/{export_id}'
            else:
                op_name = export_id

            # Try getOperation first (newer API)
            try:
                status = ee.data.getOperation(op_name)
                done   = status.get('done', False)
                error  = status.get('error', {})
                meta   = status.get('metadata', {})

                if done and not error:
                    dest = meta.get('destinationUris', [None])
                    uri  = dest[0] if dest else None
                    # Convert browser console URL to gs:// path
                    uri  = _to_gcs_uri(uri, meta)
                    logger.info(f"Export {export_id} completed — {uri}")
                    return {'status': 'completed', 'export_url': uri}
                elif done and error:
                    msg = error.get('message', 'Unknown error')
                    logger.error(f"Export {export_id} failed: {msg}")
                    return {'status': 'failed', 'error': msg}
                else:
                    state = meta.get('state', 'RUNNING')
                    logger.info(f"Export {export_id} state: {state}")
                    return {'status': 'running', 'state': state}

            except Exception as e1:
                logger.debug(f"getOperation failed ({e1}), trying listOperations")

            # Fallback: scan listOperations
            try:
                tasks   = ee.data.listOperations()
                matched = next((t for t in tasks if export_id in t.get('name', '')), None)
                if matched:
                    done  = matched.get('done', False)
                    error = matched.get('error', {})
                    meta  = matched.get('metadata', {})
                    if done and not error:
                        dest = meta.get('destinationUris', [None])
                        uri  = _to_gcs_uri(dest[0] if dest else None, meta)
                        return {'status': 'completed', 'export_url': uri}
                    elif done and error:
                        return {'status': 'failed', 'error': error.get('message', 'Unknown')}
                    else:
                        return {'status': 'running', 'state': meta.get('state', 'RUNNING')}
                else:
                    return {'status': 'running', 'state': 'PENDING'}
            except Exception as e2:
                logger.error(f"listOperations failed: {e2}")
                return {'status': 'running', 'state': 'UNKNOWN'}

        except Exception as e:
            logger.error(f"Export monitoring error: {str(e)}")
            return {'status': 'error', 'error': str(e)}

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