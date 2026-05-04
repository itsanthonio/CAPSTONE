"""
Post-processing Service for converting model probability masks into GeoJSON polygons.

This service handles:
- Thresholding probability masks to binary masks
- Extracting polygons from binary masks
- Converting pixel coordinates to real-world coordinates
- Calculating polygon areas and confidence scores
- Saving results to database
"""

import logging
import numpy as np
import rasterio
from rasterio import features
from rasterio.transform import Affine
from shapely.geometry import shape, Polygon
from shapely.ops import unary_union
import json
from typing import Tuple, List, Dict, Any, Optional
from datetime import datetime

from apps.results.models import Result
from apps.jobs.models import Job

logger = logging.getLogger(__name__)


class PostProcessor:
    """
    Service for post-processing model probability masks into detection polygons.
    
    This service converts probability masks from the inference service into
    GeoJSON FeatureCollection with polygons, confidence scores, and areas.
    """
    
    def __init__(self, threshold: float = 0.5, min_area: float = 100.0):
        """
        Initialize the post-processor.
        
        Args:
            threshold: Probability threshold for binary classification (default: 0.5)
            min_area: Minimum polygon area in square meters (default: 100.0)
        """
        self.threshold = threshold
        self.min_area = min_area
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
    
    def threshold_mask(self, probability_mask: np.ndarray) -> np.ndarray:
        """
        Apply threshold to probability mask to create binary mask.
        
        Args:
            probability_mask: 2D array with values in [0, 1]
            
        Returns:
            Binary mask with values 0 or 1
        """
        if probability_mask.min() < 0 or probability_mask.max() > 1:
            self.logger.warning(f"Probability mask values outside [0,1] range: [{probability_mask.min()}, {probability_mask.max()}]")
        
        binary_mask = (probability_mask >= self.threshold).astype(np.uint8)
        self.logger.info(f"Applied threshold {self.threshold}, positive pixels: {binary_mask.sum()}")
        
        return binary_mask
    
    def _is_projected_crs(self, source_crs) -> bool:
        """Return True if source_crs is a projected (meter-based) CRS."""
        if source_crs is None:
            return False
        try:
            from rasterio.crs import CRS
            return CRS.from_user_input(source_crs).is_projected
        except Exception:
            return False

    def _reproject_to_wgs84(self, shapely_geom, source_crs):
        """Reproject a shapely geometry from source_crs to EPSG:4326."""
        from pyproj import Transformer
        from shapely.ops import transform as shapely_transform
        from rasterio.crs import CRS
        transformer = Transformer.from_crs(
            CRS.from_user_input(source_crs), 'EPSG:4326', always_xy=True
        )
        return shapely_transform(transformer.transform, shapely_geom)

    def extract_polygons(self, binary_mask: np.ndarray, transform: Affine,
                         source_crs=None) -> List[Dict[str, Any]]:
        """
        Extract polygons from binary mask using rasterio.features.shapes.

        Args:
            binary_mask: 2D binary array
            transform: Affine transformation matrix from original GeoTIFF
            source_crs: CRS of the raster (rasterio CRS object or EPSG string).
                        Polygons are always returned in EPSG:4326.

        Returns:
            List of polygon dictionaries with geometry in WGS84 and properties
        """
        try:
            shapes = features.shapes(binary_mask, transform=transform, mask=None)

            projected = self._is_projected_crs(source_crs)
            # Simplify tolerance: 1 pixel width in CRS units
            # Projected (metres): 30 m  |  Geographic (degrees): 30/111320 ≈ 0.000270°
            simplify_tolerance = 30.0 if projected else 0.000270

            polygons = []
            for geom, value in shapes:
                if value != 1:
                    continue
                shapely_geom = shape(geom)

                shapely_geom = shapely_geom.simplify(simplify_tolerance, preserve_topology=True)
                if shapely_geom.is_empty:
                    continue

                # Area in m²: projected CRS gives m² directly; geographic needs conversion
                if projected:
                    area_m2 = shapely_geom.area
                else:
                    import math
                    area_deg = shapely_geom.area
                    centroid_lat = shapely_geom.centroid.y
                    area_m2 = area_deg * 111320.0 * (111320.0 * math.cos(math.radians(centroid_lat)))

                if area_m2 < self.min_area:
                    continue

                # Reproject to WGS84 for storage (no-op if already geographic)
                geom_wgs84 = self._reproject_to_wgs84(shapely_geom, source_crs) if projected else shapely_geom

                polygons.append({
                    'geometry': geom_wgs84.__geo_interface__,
                    'shapely_geometry': shapely_geom,   # native CRS — used for pixel ops
                    'shapely_geometry_wgs84': geom_wgs84,
                    'area': area_m2,
                    'value': value,
                })

            self.logger.info(f"Extracted {len(polygons)} polygons above minimum area {self.min_area} m²")
            return polygons

        except Exception as e:
            self.logger.error(f"Failed to extract polygons: {str(e)}")
            raise
    
    def calculate_confidence_scores(self, polygons: List[Dict[str, Any]],
                               probability_mask: np.ndarray,
                               transform: Affine,
                               source_crs=None) -> List[Dict[str, Any]]:
        """
        Calculate confidence scores for each polygon using the original probability mask.
        Also computes the hotspot coordinate (WGS84 lon/lat): the pixel of maximum
        probability within each polygon.

        Args:
            polygons: List of polygon dictionaries (shapely_geometry in raster CRS)
            probability_mask: Original probability mask
            transform: Affine transform of the raster (same one used to extract polygons)
            source_crs: CRS of the raster; hotspot is reprojected to WGS84 when projected.

        Returns:
            Updated polygon list with confidence scores and WGS84 hotspot coords
        """
        from rasterio.features import rasterize

        projected = self._is_projected_crs(source_crs)

        for polygon in polygons:
            # Use native-CRS geometry for pixel operations (rasterize / argmax)
            shapely_geom = polygon['shapely_geometry']

            polygon_mask = rasterize(
                [(shapely_geom, 1)],
                out_shape=probability_mask.shape,
                transform=transform,
            ).astype(bool)

            if polygon_mask.sum() > 0:
                confidence = float(probability_mask[polygon_mask].mean())

                masked_prob = np.where(polygon_mask, probability_mask, -1)
                max_row, max_col = np.unravel_index(masked_prob.argmax(), masked_prob.shape)
                # Pixel centre in raster CRS
                hotspot_x, hotspot_y = transform * (max_col + 0.5, max_row + 0.5)
            else:
                confidence = 0.0
                c = shapely_geom.centroid
                hotspot_x, hotspot_y = c.x, c.y

            # Convert to WGS84 lon/lat if the raster is in a projected CRS
            if projected and source_crs is not None:
                from pyproj import Transformer
                from rasterio.crs import CRS
                t = Transformer.from_crs(CRS.from_user_input(source_crs), 'EPSG:4326', always_xy=True)
                hotspot_lon, hotspot_lat = t.transform(hotspot_x, hotspot_y)
            else:
                hotspot_lon, hotspot_lat = hotspot_x, hotspot_y

            polygon['confidence_score'] = confidence
            polygon['hotspot_lon'] = hotspot_lon
            polygon['hotspot_lat'] = hotspot_lat

        return polygons
    
    def create_geojson_featurecollection(self, polygons: List[Dict[str, Any]], 
                                    job_id: str, model_version: str = "unknown") -> Dict[str, Any]:
        """
        Create GeoJSON FeatureCollection from polygons.
        
        Args:
            polygons: List of polygon dictionaries
            job_id: Job identifier
            model_version: Model version string
            
        Returns:
            GeoJSON FeatureCollection dictionary
        """
        features = []
        total_area = 0.0
        
        for i, polygon in enumerate(polygons):
            feature = {
                "type": "Feature",
                "geometry": polygon['geometry'],
                "properties": {
                    "id": f"detection_{i}",
                    "confidence_score": polygon['confidence_score'],
                    "area": polygon['area'],
                    "area_hectares": polygon['area'] / 10000.0,  # Convert m² to hectares
                    "hotspot_lon": polygon.get('hotspot_lon'),
                    "hotspot_lat": polygon.get('hotspot_lat'),
                    "job_id": job_id,
                    "model_version": model_version,
                    "threshold": self.threshold,
                    "created_at": datetime.utcnow().isoformat()
                }
            }
            features.append(feature)
            total_area += polygon['area']
        
        feature_collection = {
            "type": "FeatureCollection",
            "features": features,
            "properties": {
                "total_detections": len(features),
                "total_area_m2": total_area,
                "total_area_hectares": total_area / 10000.0,
                "threshold_used": self.threshold,
                "min_area_m2": self.min_area,
                "job_id": job_id,
                "model_version": model_version,
                "created_at": datetime.utcnow().isoformat()
            }
        }
        
        self.logger.info(f"Created GeoJSON with {len(features)} features, total area: {total_area:.2f} m²")
        return feature_collection
    
    def save_results(self, geojson_featurecollection: Dict[str, Any], 
                   job: Job, tile_reference: str = "unknown") -> Result:
        """
        Save post-processing results to database.
        
        Args:
            geojson_featurecollection: GeoJSON FeatureCollection
            job: Job instance
            tile_reference: Reference to source tiles
            
        Returns:
            Created Result instance
        """
        try:
            # Extract summary statistics
            properties = geojson_featurecollection['properties']
            summary_statistics = {
                "total_detections": properties['total_detections'],
                "total_area_m2": properties['total_area_m2'],
                "total_area_hectares": properties['total_area_hectares'],
                "threshold_used": properties['threshold_used'],
                "min_area_m2": properties['min_area_m2'],
                "confidence_distribution": self._calculate_confidence_distribution(geojson_featurecollection)
            }
            
            # Create Result record
            result = Result.objects.create(
                job=job,
                geojson=geojson_featurecollection,
                tile_reference=tile_reference,
                summary_statistics=summary_statistics,
                total_area_detected=properties['total_area_hectares']
            )
            
            self.logger.info(f"Saved results for job {job.id}: {properties['total_detections']} detections, {properties['total_area_hectares']:.2f} ha")
            return result
            
        except Exception as e:
            self.logger.error(f"Failed to save results for job {job.id}: {str(e)}")
            raise
    
    def _calculate_confidence_distribution(self, geojson_featurecollection: Dict[str, Any]) -> Dict[str, float]:
        """Calculate confidence score distribution statistics."""
        features = geojson_featurecollection.get('features', [])
        if not features:
            return {"mean": 0.0, "min": 0.0, "max": 0.0, "std": 0.0}
        
        confidences = [f['properties']['confidence_score'] for f in features]
        return {
            "mean": float(np.mean(confidences)),
            "min": float(np.min(confidences)),
            "max": float(np.max(confidences)),
            "std": float(np.std(confidences))
        }
    
    def process_probability_mask(self, probability_mask: np.ndarray,
                            transform: Affine, job: Job,
                            model_version: str = "unknown",
                            tile_reference: str = "unknown",
                            source_crs=None) -> Result:
        """
        Main post-processing pipeline: convert probability mask to saved results.

        Args:
            probability_mask: 2D probability mask from inference service
            transform: Affine transformation from original GeoTIFF
            job: Job instance
            model_version: Model version string
            tile_reference: Reference to source tiles
            source_crs: CRS of the raster (rasterio CRS or EPSG string).
                        Polygons are always output in WGS84 regardless of input CRS.

        Returns:
            Created Result instance
        """
        try:
            self.logger.info(f"Starting post-processing for job {job.id}")

            if probability_mask.ndim == 3 and probability_mask.shape[0] == 1:
                probability_mask = probability_mask.squeeze(0)

            binary_mask = self.threshold_mask(probability_mask)
            polygons = self.extract_polygons(binary_mask, transform, source_crs=source_crs)

            if not polygons:
                self.logger.info(f"No detections found for job {job.id}")
                empty_geojson = {
                    "type": "FeatureCollection",
                    "features": [],
                    "properties": {
                        "total_detections": 0,
                        "total_area_m2": 0.0,
                        "total_area_hectares": 0.0,
                        "threshold_used": self.threshold,
                        "min_area_m2": self.min_area,
                        "job_id": str(job.id),
                        "model_version": model_version,
                        "created_at": datetime.utcnow().isoformat()
                    }
                }
                return self.save_results(empty_geojson, job, tile_reference)

            polygons = self.calculate_confidence_scores(
                polygons, probability_mask, transform, source_crs=source_crs
            )
            geojson_fc = self.create_geojson_featurecollection(polygons, str(job.id), model_version)
            result = self.save_results(geojson_fc, job, tile_reference)

            self.logger.info(f"Post-processing completed for job {job.id}")
            return result

        except Exception as e:
            self.logger.error(f"Post-processing failed for job {job.id}: {str(e)}")
            raise


def save_patch_images(job, tensor: np.ndarray, probability_mask: np.ndarray,
                      sites=None, raster_meta: dict = None) -> bool:
    """
    Generate 4 ML visualization PNGs.

    Always saves whole-AOI images on the Job record (overview / backward compat).
    If sites + raster_meta are provided, also saves per-site cropped images
    on each DetectedSite record so the UI shows the exact detection polygon.

    Tensor band order (from PreprocessingService.MODEL_BAND_ORDER):
        0: B3 (Green), 1: B4 (Red), 2: B8 (NIR),
        3: B11 (SWIR1), 4: B12 (SWIR2), 5: BSI

    Returns True on success, False if anything fails.
    """
    try:
        from pathlib import Path
        from PIL import Image
        from django.conf import settings

        MIN_SIDE = 1024  # minimum output dimension (upscale tiny rasters)
        PAD = 15         # extra pixel padding around each site crop

        job_id = str(job.id)

        # ── Prepare arrays ────────────────────────────────────────────────
        if probability_mask.ndim == 3 and probability_mask.shape[0] == 1:
            prob = probability_mask.squeeze(0)
        else:
            prob = probability_mask

        t = tensor.astype(np.float32)
        h, w = prob.shape

        def _stretch(arr):
            """Percentile stretch to [0, 1]: excludes inf/nan then clips to [p2, p98]."""
            valid = arr[np.isfinite(arr)]
            if len(valid) < 10:
                return np.full_like(arr, 0.5)
            lo, hi = np.percentile(valid, 2), np.percentile(valid, 98)
            if hi <= lo:
                return np.full_like(arr, 0.5)
            return np.clip((arr - lo) / (hi - lo), 0.0, 1.0)

        def _hot(arr_2d):
            """Apply 'hot' colourmap to a (H,W) array in [0,1]. Returns (H,W,3) uint8."""
            v = np.clip(arr_2d, 0.0, 1.0)
            r = np.clip(v * 3.0,       0.0, 1.0)
            g = np.clip(v * 3.0 - 1.0, 0.0, 1.0)
            b = np.clip(v * 3.0 - 2.0, 0.0, 1.0)
            return (np.stack([r, g, b], axis=-1) * 255).astype(np.uint8)

        def _upscale(img: Image.Image, resample=Image.LANCZOS) -> Image.Image:
            if img.width >= MIN_SIDE and img.height >= MIN_SIDE:
                return img
            scale = max(MIN_SIDE / img.width, MIN_SIDE / img.height)
            new_w = max(MIN_SIDE, int(img.width  * scale))
            new_h = max(MIN_SIDE, int(img.height * scale))
            return img.resize((new_w, new_h), resample)

        def _save_img(arr_uint8, path: Path, resample=Image.LANCZOS):
            # Use LANCZOS for photo-like imagery (smooth), NEAREST for binary masks (sharp edges)
            _upscale(Image.fromarray(arr_uint8), resample).save(str(path))

        def _make_four(t_crop, p_crop):
            """Build the 4 image arrays from cropped tensor + probability."""
            rc = _stretch(t_crop[1])   # B4 = Red
            gc = _stretch(t_crop[0])   # B3 = Green
            bc = _stretch(t_crop[2])   # B8 = NIR
            fc = (np.stack([rc, gc, bc], axis=-1) * 255).astype(np.uint8)
            bin_c = (p_crop >= 0.5).astype(np.float32)
            base  = fc.astype(np.float32)
            ov    = base.copy()
            m     = bin_c.astype(bool)
            ov[m, 0] = base[m, 0] * 0.5 + 127.5
            ov[m, 1] = base[m, 1] * 0.5
            ov[m, 2] = base[m, 2] * 0.5
            return fc, bin_c, p_crop, np.clip(ov, 0, 255).astype(np.uint8)

        # ── 1. Whole-AOI images (saved on Job) ────────────────────────────
        out_dir = Path(settings.MEDIA_ROOT) / 'job_images' / job_id
        out_dir.mkdir(parents=True, exist_ok=True)

        fc, binary, _, overlay_arr = _make_four(t, prob)
        _save_img(fc,                          out_dir / 'false_color.png')
        _save_img(_hot(binary),                out_dir / 'prediction_mask.png',   Image.NEAREST)
        _save_img(_hot(prob),                  out_dir / 'probability_heatmap.png')
        _save_img(overlay_arr,                 out_dir / 'overlay.png')

        rel = f'job_images/{job_id}/'
        job.img_false_color         = rel + 'false_color.png'
        job.img_prediction_mask     = rel + 'prediction_mask.png'
        job.img_probability_heatmap = rel + 'probability_heatmap.png'
        job.img_overlay             = rel + 'overlay.png'
        job.save(update_fields=[
            'img_false_color', 'img_prediction_mask',
            'img_probability_heatmap', 'img_overlay',
        ])
        logger.info(f"Saved whole-AOI patch images for job {job_id}")

        # ── 2. Per-site cropped images (saved on DetectedSite) ────────────
        if sites and raster_meta and 'transform' in raster_meta:
            from rasterio.transform import rowcol as _rowcol
            transform = raster_meta['transform']

            for site in sites:
                try:
                    # site.geometry.extent → (minx, miny, maxx, maxy) in WGS84
                    minx, miny, maxx, maxy = site.geometry.extent

                    # Geographic coordinates → pixel row/col
                    r0, c0 = _rowcol(transform, minx, maxy)  # top-left corner
                    r1, c1 = _rowcol(transform, maxx, miny)  # bottom-right corner

                    # Clamp to raster bounds and add padding
                    r_min = max(0, min(r0, r1) - PAD)
                    r_max = min(h, max(r0, r1) + PAD)
                    c_min = max(0, min(c0, c1) - PAD)
                    c_max = min(w, max(c0, c1) + PAD)

                    if r_max <= r_min or c_max <= c_min:
                        logger.warning(f"Empty crop for site {site.id}, skipping")
                        continue

                    # Crop both tensor and probability mask to this site
                    t_crop = t[:, r_min:r_max, c_min:c_max]
                    p_crop = prob[r_min:r_max, c_min:c_max]

                    fc_s, bin_s, p_s, ov_s = _make_four(t_crop, p_crop)

                    site_id = str(site.id)
                    s_dir = Path(settings.MEDIA_ROOT) / 'site_images' / site_id
                    s_dir.mkdir(parents=True, exist_ok=True)

                    _save_img(fc_s,                    s_dir / 'false_color.png')
                    _save_img(_hot(bin_s),             s_dir / 'prediction_mask.png',   Image.NEAREST)
                    _save_img(_hot(p_s),               s_dir / 'probability_heatmap.png')
                    _save_img(ov_s,                    s_dir / 'overlay.png')

                    s_rel = f'site_images/{site_id}/'
                    site.img_false_color         = s_rel + 'false_color.png'
                    site.img_prediction_mask     = s_rel + 'prediction_mask.png'
                    site.img_probability_heatmap = s_rel + 'probability_heatmap.png'
                    site.img_overlay             = s_rel + 'overlay.png'
                    site.save(update_fields=[
                        'img_false_color', 'img_prediction_mask',
                        'img_probability_heatmap', 'img_overlay',
                    ])
                    logger.info(f"Saved per-site images for site {site_id}")

                except Exception as exc:
                    logger.warning(f"Failed per-site images for site {getattr(site, 'id', '?')}: {exc}")

        return True

    except Exception as exc:
        logger.warning(f"Failed to save patch images for job {job.id}: {exc}")
        return False


# Singleton instance for the service
_postprocessor = None


def get_postprocessor(threshold: float = 0.5, min_area: float = 100.0) -> PostProcessor:
    """
    Get singleton instance of the post-processor.
    
    Args:
        threshold: Probability threshold for binary classification
        min_area: Minimum polygon area in square meters
        
    Returns:
        PostProcessor instance
    """
    global _postprocessor
    if _postprocessor is None:
        _postprocessor = PostProcessor(threshold, min_area)
    return _postprocessor