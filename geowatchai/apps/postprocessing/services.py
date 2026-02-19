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
    
    def extract_polygons(self, binary_mask: np.ndarray, transform: Affine) -> List[Dict[str, Any]]:
        """
        Extract polygons from binary mask using rasterio.features.shapes.
        
        Args:
            binary_mask: 2D binary array
            transform: Affine transformation matrix from original GeoTIFF
            
        Returns:
            List of polygon dictionaries with geometry and properties
        """
        try:
            # Extract shapes from binary mask
            shapes = features.shapes(
                binary_mask,
                transform=transform,
                mask=None
            )
            
            polygons = []
            for geom, value in shapes:
                if value == 1:  # Only keep positive shapes
                    # Convert to shapely geometry for area calculation
                    shapely_geom = shape(geom)
                    
                    # Calculate area in square meters
                    # shapely.area is in CRS units (degrees for WGS84)
                    # Convert to m² using rough approximation: 1 deg ≈ 111,320 m at equator
                    area_deg = shapely_geom.area
                    centroid_lat = shapely_geom.centroid.y
                    import math
                    m_per_deg_lat = 111320.0
                    m_per_deg_lon = 111320.0 * math.cos(math.radians(centroid_lat))
                    area_m2 = area_deg * m_per_deg_lat * m_per_deg_lon

                    # Filter by minimum area
                    if area_m2 >= self.min_area:
                        polygons.append({
                            'geometry': geom,
                            'shapely_geometry': shapely_geom,
                            'area': area_m2,
                            'value': value
                        })
            
            self.logger.info(f"Extracted {len(polygons)} polygons above minimum area {self.min_area} m²")
            return polygons
            
        except Exception as e:
            self.logger.error(f"Failed to extract polygons: {str(e)}")
            raise
    
    def calculate_confidence_scores(self, polygons: List[Dict[str, Any]], 
                               probability_mask: np.ndarray) -> List[Dict[str, Any]]:
        """
        Calculate confidence scores for each polygon using the original probability mask.
        
        Args:
            polygons: List of polygon dictionaries
            probability_mask: Original probability mask
            
        Returns:
            Updated polygon list with confidence scores
        """
        for polygon in polygons:
            shapely_geom = polygon['shapely_geometry']
            
            # Create a mask for this polygon to extract mean probability
            from rasterio.features import rasterize
            polygon_mask = rasterize(
                [(shapely_geom, 1)],
                out_shape=probability_mask.shape,
                transform=Affine(1, 0, 0, 0, -1, 0)  # Identity transform for pixel coordinates
            ).astype(bool)
            
            # Calculate mean probability within polygon
            if polygon_mask.sum() > 0:
                confidence = probability_mask[polygon_mask].mean()
            else:
                confidence = 0.0
            
            polygon['confidence_score'] = float(confidence)
        
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
                            tile_reference: str = "unknown") -> Result:
        """
        Main post-processing pipeline: convert probability mask to saved results.
        
        Args:
            probability_mask: 2D probability mask from inference service
            transform: Affine transformation from original GeoTIFF
            job: Job instance
            model_version: Model version string
            tile_reference: Reference to source tiles
            
        Returns:
            Created Result instance
        """
        try:
            self.logger.info(f"Starting post-processing for job {job.id}")

            # Squeeze channel dim if present: (1, H, W) -> (H, W)
            if probability_mask.ndim == 3 and probability_mask.shape[0] == 1:
                probability_mask = probability_mask.squeeze(0)

            # Step 1: Apply threshold
            binary_mask = self.threshold_mask(probability_mask)
            
            # Step 2: Extract polygons
            polygons = self.extract_polygons(binary_mask, transform)
            
            # Handle case with no detections
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
            
            # Step 3: Calculate confidence scores
            polygons = self.calculate_confidence_scores(polygons, probability_mask)
            
            # Step 4: Create GeoJSON FeatureCollection
            geojson_fc = self.create_geojson_featurecollection(polygons, str(job.id), model_version)
            
            # Step 5: Save to database
            result = self.save_results(geojson_fc, job, tile_reference)
            
            self.logger.info(f"Post-processing completed for job {job.id}")
            return result
            
        except Exception as e:
            self.logger.error(f"Post-processing failed for job {job.id}: {str(e)}")
            raise


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