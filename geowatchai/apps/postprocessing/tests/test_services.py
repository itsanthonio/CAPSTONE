"""
Unit tests for Post-processing Service.

Tests cover:
- Thresholding probability masks to binary masks
- Polygon extraction using rasterio.features.shapes
- Coordinate transformation with affine transforms
- Area calculation and confidence scoring
- GeoJSON FeatureCollection creation
- Database result saving
- Edge case handling (no detections, small polygons)
"""

import pytest
import numpy as np
import json
from unittest.mock import patch, MagicMock
from rasterio.transform import Affine
from datetime import datetime

# Set up Django before importing test classes
import django
from django.conf import settings
if not settings.configured:
    import os
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    django.setup()

from django.test import TestCase
from apps.postprocessing.services import PostProcessor, get_postprocessor
from apps.jobs.models import Job
from apps.results.models import Result


class PostProcessorTest(TestCase):
    """Test post-processing service functionality"""
    
    def setUp(self):
        """Set up test fixtures before each test method."""
        self.postprocessor = PostProcessor(threshold=0.5, min_area=100.0)
        
        # Create test job
        self.job = Job.objects.create(
            aoi_geometry='POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))',
            start_date='2024-01-01',
            end_date='2024-01-31',
            model_version='test_v1.0'
        )
        
        # Create test probability mask with a known hotspot
        self.probability_mask = np.zeros((256, 256), dtype=np.float32)
        # Add a square hotspot with high probability
        self.probability_mask[100:150, 100:150] = 0.8  # 50x50 square
        
        # Create affine transform (identity for pixel coordinates)
        self.transform = Affine.from_gdal((256, 256))
    
    def test_threshold_mask_success(self):
        """Test successful threshold application."""
        binary_mask = self.postprocessor.threshold_mask(self.probability_mask)
        
        # Should be binary (0 or 1)
        unique_values = np.unique(binary_mask)
        self.assertTrue(np.all(np.isin(unique_values, [0, 1])))
        
        # High probability area should be 1
        self.assertEqual(binary_mask[125, 125], 1)
        
        # Low probability area should be 0
        self.assertEqual(binary_mask[50, 50], 0)
    
    def test_threshold_mask_warning(self):
        """Test threshold mask with out-of-range values."""
        # Create mask with values outside [0, 1]
        bad_mask = np.array([[-0.1, 1.2], [0.5, 0.3]])
        
        with patch.object(self.postprocessor.logger, 'warning') as mock_warning:
            binary_mask = self.postprocessor.threshold_mask(bad_mask)
            mock_warning.assert_called_once()
    
    def test_extract_polygons_success(self):
        """Test successful polygon extraction."""
        binary_mask = (self.probability_mask >= 0.5).astype(np.uint8)
        polygons = self.postprocessor.extract_polygons(binary_mask, self.transform)
        
        # Should extract one polygon (the hotspot)
        self.assertEqual(len(polygons), 1)
        
        polygon = polygons[0]
        self.assertIn('geometry', polygon)
        self.assertIn('shapely_geometry', polygon)
        self.assertIn('area', polygon)
        self.assertIn('value', polygon)
        
        # Area should be around 2500 (50x50 pixels)
        self.assertGreater(polygon['area'], 2000)
        self.assertLess(polygon['area'], 3000)
    
    def test_extract_polygons_min_area_filter(self):
        """Test polygon extraction with minimum area filtering."""
        # Create small polygon that should be filtered out
        small_mask = np.zeros((256, 256), dtype=np.uint8)
        small_mask[100:102, 100:102] = 1  # 2x2 square = 4 pixels
        
        polygons = self.postprocessor.extract_polygons(small_mask, self.transform)
        
        # Should be filtered out due to small area
        self.assertEqual(len(polygons), 0)
    
    def test_extract_polygons_no_detections(self):
        """Test polygon extraction with no positive pixels."""
        empty_mask = np.zeros((256, 256), dtype=np.uint8)
        
        polygons = self.postprocessor.extract_polygons(empty_mask, self.transform)
        
        self.assertEqual(len(polygons), 0)
    
    def test_calculate_confidence_scores(self):
        """Test confidence score calculation."""
        # Create mock polygon
        from shapely.geometry import Polygon as ShapelyPolygon
        shapely_polygon = ShapelyPolygon([(100, 100), (150, 100), (150, 150), (100, 150)])
        
        polygons = [{
            'shapely_geometry': shapely_polygon,
            'area': 2500.0,
            'value': 1
        }]
        
        # Calculate confidence scores
        result_polygons = self.postprocessor.calculate_confidence_scores(polygons, self.probability_mask)
        
        # Should have confidence score
        self.assertIn('confidence_score', result_polygons[0])
        
        # Confidence should be high (around 0.8 for the hotspot)
        confidence = result_polygons[0]['confidence_score']
        self.assertGreater(confidence, 0.7)
        self.assertLessEqual(confidence, 1.0)
    
    def test_create_geojson_featurecollection(self):
        """Test GeoJSON FeatureCollection creation."""
        polygons = [{
            'geometry': {'type': 'Polygon', 'coordinates': [[[100, 100], [150, 100], [150, 150], [100, 150], [100, 100]]]},
            'area': 2500.0,
            'confidence_score': 0.8,
            'value': 1
        }]
        
        geojson_fc = self.postprocessor.create_geojson_featurecollection(
            polygons, 'test_job_id', 'test_model_v1.0'
        )
        
        # Check structure
        self.assertEqual(geojson_fc['type'], 'FeatureCollection')
        self.assertIn('features', geojson_fc)
        self.assertIn('properties', geojson_fc)
        
        # Check features
        features = geojson_fc['features']
        self.assertEqual(len(features), 1)
        
        feature = features[0]
        self.assertEqual(feature['type'], 'Feature')
        self.assertEqual(feature['geometry'], polygons[0]['geometry'])
        
        # Check feature properties
        props = feature['properties']
        self.assertEqual(props['id'], 'detection_0')
        self.assertEqual(props['confidence_score'], 0.8)
        self.assertEqual(props['area'], 2500.0)
        self.assertEqual(props['area_hectares'], 0.25)  # 2500 / 10000
        self.assertEqual(props['job_id'], 'test_job_id')
        self.assertEqual(props['model_version'], 'test_model_v1.0')
        self.assertEqual(props['threshold'], 0.5)
        
        # Check collection properties
        collection_props = geojson_fc['properties']
        self.assertEqual(collection_props['total_detections'], 1)
        self.assertEqual(collection_props['total_area_m2'], 2500.0)
        self.assertEqual(collection_props['total_area_hectares'], 0.25)
        self.assertEqual(collection_props['job_id'], 'test_job_id')
    
    def test_create_geojson_empty(self):
        """Test GeoJSON creation with no polygons."""
        geojson_fc = self.postprocessor.create_geojson_featurecollection(
            [], 'test_job_id', 'test_model_v1.0'
        )
        
        self.assertEqual(geojson_fc['type'], 'FeatureCollection')
        self.assertEqual(len(geojson_fc['features']), 0)
        
        props = geojson_fc['properties']
        self.assertEqual(props['total_detections'], 0)
        self.assertEqual(props['total_area_m2'], 0.0)
        self.assertEqual(props['total_area_hectares'], 0.0)
    
    @patch('apps.postprocessing.services.Result.objects.create')
    def test_save_results(self, mock_create):
        """Test saving results to database."""
        geojson_fc = {
            'type': 'FeatureCollection',
            'features': [],
            'properties': {
                'total_detections': 0,
                'total_area_m2': 0.0,
                'total_area_hectares': 0.0,
                'threshold_used': 0.5,
                'min_area_m2': 100.0
            }
        }
        
        # Mock Result creation
        mock_result = MagicMock()
        mock_create.return_value = mock_result
        
        result = self.postprocessor.save_results(geojson_fc, self.job, 'test_tile')
        
        # Verify Result.objects.create was called with correct arguments
        mock_create.assert_called_once()
        call_args = mock_create.call_args[1]
        
        self.assertEqual(call_args['job'], self.job)
        self.assertEqual(call_args['geojson'], geojson_fc)
        self.assertEqual(call_args['tile_reference'], 'test_tile')
        self.assertIn('summary_statistics', call_args)
        self.assertEqual(call_args['total_area_detected'], 0.0)
        
        self.assertEqual(result, mock_result)
    
    @patch('apps.postprocessing.services.Result.objects.create')
    def test_process_probability_mask_with_detections(self, mock_create):
        """Test full pipeline with detections."""
        # Mock Result creation
        mock_result = MagicMock()
        mock_create.return_value = mock_result
        
        # Process the test mask
        result = self.postprocessor.process_probability_mask(
            self.probability_mask, self.transform, self.job, 'test_model_v1.0', 'test_tile'
        )
        
        # Should return Result instance
        self.assertEqual(result, mock_result)
        
        # Verify Result.objects.create was called
        mock_create.assert_called_once()
        
        # Check that geojson was created with detections
        call_args = mock_create.call_args[1]
        geojson = call_args['geojson']
        self.assertEqual(geojson['properties']['total_detections'], 1)
    
    @patch('apps.postprocessing.services.Result.objects.create')
    def test_process_probability_mask_no_detections(self, mock_create):
        """Test full pipeline with no detections."""
        # Create empty probability mask
        empty_mask = np.zeros((256, 256), dtype=np.float32)
        
        # Mock Result creation
        mock_result = MagicMock()
        mock_create.return_value = mock_result
        
        result = self.postprocessor.process_probability_mask(
            empty_mask, self.transform, self.job, 'test_model_v1.0', 'test_tile'
        )
        
        # Should return Result instance
        self.assertEqual(result, mock_result)
        
        # Verify Result.objects.create was called with empty geojson
        mock_create.assert_called_once()
        call_args = mock_create.call_args[1]
        geojson = call_args['geojson']
        self.assertEqual(geojson['properties']['total_detections'], 0)
        self.assertEqual(len(geojson['features']), 0)
    
    def test_calculate_confidence_distribution(self):
        """Test confidence distribution calculation."""
        geojson_fc = {
            'features': [
                {'properties': {'confidence_score': 0.8}},
                {'properties': {'confidence_score': 0.6}},
                {'properties': {'confidence_score': 0.9}}
            ]
        }
        
        distribution = self.postprocessor._calculate_confidence_distribution(geojson_fc)
        
        self.assertAlmostEqual(distribution['mean'], 0.7666666667, places=5)
        self.assertEqual(distribution['min'], 0.6)
        self.assertEqual(distribution['max'], 0.9)
        self.assertAlmostEqual(distribution['std'], 0.1527525, places=5)
    
    def test_calculate_confidence_distribution_empty(self):
        """Test confidence distribution with no features."""
        geojson_fc = {'features': []}
        
        distribution = self.postprocessor._calculate_confidence_distribution(geojson_fc)
        
        self.assertEqual(distribution['mean'], 0.0)
        self.assertEqual(distribution['min'], 0.0)
        self.assertEqual(distribution['max'], 0.0)
        self.assertEqual(distribution['std'], 0.0)


class PostProcessorSingletonTest(TestCase):
    """Test post-processor singleton pattern"""
    
    def setUp(self):
        """Clear singleton before tests."""
        import apps.postprocessing.services
        apps.postprocessing.services._postprocessor = None
    
    def test_get_postprocessor_singleton(self):
        """Test that get_postprocessor returns the same instance."""
        processor1 = get_postprocessor()
        processor2 = get_postprocessor()
        
        self.assertIs(processor1, processor2)
        self.assertIsInstance(processor1, PostProcessor)
    
    def test_get_postprocessor_custom_parameters(self):
        """Test get_postprocessor with custom parameters."""
        processor = get_postprocessor(threshold=0.7, min_area=200.0)
        
        self.assertEqual(processor.threshold, 0.7)
        self.assertEqual(processor.min_area, 200.0)


class PostProcessorIntegrationTest(TestCase):
    """Integration tests for post-processing service"""
    
    def setUp(self):
        """Set up integration test fixtures."""
        self.job = Job.objects.create(
            aoi_geometry='POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))',
            start_date='2024-01-01',
            end_date='2024-01-31',
            model_version='test_v1.0'
        )
    
    def test_full_pipeline_integration(self):
        """Test complete post-processing pipeline integration."""
        # Create realistic probability mask
        prob_mask = np.random.rand(128, 128).astype(np.float32)
        # Add some high-probability regions
        prob_mask[20:40, 20:40] = 0.9  # Square 1
        prob_mask[60:80, 60:80] = 0.8  # Square 2
        
        # Create transform
        transform = Affine.from_gdal((128, 128))
        
        # Process with post-processor
        processor = PostProcessor(threshold=0.5, min_area=50.0)
        
        with patch('apps.postprocessing.services.Result.objects.create') as mock_create:
            mock_result = MagicMock(id='test-result-id')
            mock_create.return_value = mock_result
            
            result = processor.process_probability_mask(
                prob_mask, transform, self.job, 'test_model_v1.0', 'test_tile'
            )
            
            # Should have created result
            self.assertEqual(result, mock_result)
            
            # Should have called Result.objects.create
            mock_create.assert_called_once()
            
            # Check geojson structure
            call_args = mock_create.call_args[1]
            geojson = call_args['geojson']
            
            self.assertEqual(geojson['type'], 'FeatureCollection')
            self.assertIn('features', geojson)
            self.assertIn('properties', geojson)
            
            # Should have detections (the two squares)
            self.assertGreater(len(geojson['features']), 0)
            
            # Check properties
            props = geojson['properties']
            self.assertEqual(props['job_id'], str(self.job.id))
            self.assertEqual(props['model_version'], 'test_model_v1.0')
            self.assertEqual(props['threshold_used'], 0.5)
            self.assertEqual(props['min_area_m2'], 50.0)
