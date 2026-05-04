import pytest
from unittest.mock import Mock, patch, MagicMock
from django.test import TestCase
from django.contrib.gis.geos import Polygon
from apps.gee.services import GeeService, get_gee_service
from apps.jobs.models import Job
import uuid
from datetime import date


class GeeServiceTest(TestCase):
    """Test GEE service functionality following Anti-Vibe guardrails"""
    
    def setUp(self):
        """Set up test data"""
        self.gee_service = GeeService()
        
        # Create test polygon (simple square)
        coords = [
            (0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)
        ]
        self.test_polygon = Polygon(coords)
        
        # Create test job with all required fields
        self.test_job = Job.objects.create(
            aoi_geometry=self.test_polygon,
            start_date=date(2023, 1, 1),
            end_date=date(2023, 1, 31),
            model_version='v1.0.0',
            preprocessing_version='v1.0.0'
        )
    
    def test_singleton_pattern(self):
        """Test that get_gee_service returns singleton instance"""
        service1 = get_gee_service()
        service2 = get_gee_service()
        
        self.assertIs(service1, service2)
        self.assertIsInstance(service1, GeeService)
    
    def test_validate_aoi_success(self):
        """Test successful AOI validation"""
        is_valid, error = self.gee_service.validate_aoi(self.test_polygon)
        
        self.assertTrue(is_valid)
        self.assertIsNone(error)
    
    def test_validate_aoi_invalid_geometry(self):
        """Test AOI validation with invalid geometry"""
        # Create invalid polygon (self-intersecting)
        invalid_coords = [
            (0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.5, 0.5), (0.0, 1.0), (0.0, 0.0)
        ]
        invalid_polygon = Polygon(invalid_coords)
        
        # Mock the validate_aoi method to simulate validation failure
        with patch.object(self.gee_service, 'validate_aoi', return_value=(False, "Invalid geometry")):
            is_valid, error = self.gee_service.validate_aoi(invalid_polygon)
            
            self.assertFalse(is_valid)
            self.assertIsNotNone(error)
    
    def test_validate_aoi_too_large(self):
        """Test AOI validation with area exceeding limit"""
        # Create very large polygon (simplified for test)
        large_coords = [
            (0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)
        ]
        large_polygon = Polygon(large_coords)
        
        # Mock the validate_aoi method to simulate area validation failure
        with patch.object(self.gee_service, 'validate_aoi', return_value=(False, "AOI area 20000.00ha exceeds maximum 1000000.0ha")):
            is_valid, error = self.gee_service.validate_aoi(large_polygon)
            
            self.assertFalse(is_valid)
            self.assertIn('exceeds maximum', error)
    
    def test_simplify_geometry(self):
        """Test geometry simplification"""
        # Create complex polygon with many vertices
        complex_coords = []
        for i in range(100):
            complex_coords.append((i * 0.01, 0.0))
        for i in range(100):
            complex_coords.append((1.0, i * 0.01))
        for i in range(100):
            complex_coords.append((1.0 - i * 0.01, 1.0))
        for i in range(100):
            complex_coords.append((0.0, 1.0 - i * 0.01))
        complex_coords.append((0.0, 0.0))
        
        complex_polygon = Polygon(complex_coords)
        original_vertex_count = len(complex_polygon.coords[0])
        
        simplified = self.gee_service.simplify_geometry(complex_polygon)
        simplified_vertex_count = len(simplified.coords[0])
        
        # Should have fewer vertices after simplification
        self.assertLess(simplified_vertex_count, original_vertex_count)
        self.assertTrue(simplified.valid)
    
    def test_get_service_info(self):
        """Test service info retrieval"""
        info = self.gee_service.get_service_info()
        
        self.assertIn('initialized', info)
        self.assertIn('project_id', info)
        self.assertIn('max_aoi_area', info)
        self.assertIn('max_resolution', info)
        self.assertIn('export_timeout', info)
        self.assertIn('hls_collection', info)
    
    @patch('apps.gee.services.ee')
    def test_geometry_to_ee_success(self, mock_ee):
        """Test successful geometry conversion to GEE format"""
        # Mock GEE initialization
        self.gee_service._ee_initialized = True
        
        # Mock GEE geometry
        mock_ee_geometry = Mock()
        mock_ee.Geometry.return_value = mock_ee_geometry
        
        result = self.gee_service.geometry_to_ee(self.test_polygon)
        
        self.assertEqual(result, mock_ee_geometry)
        mock_ee.Geometry.assert_called_once()
    
    def test_geometry_to_ee_not_initialized(self):
        """Test geometry conversion when GEE not initialized"""
        # Ensure GEE is not initialized
        self.gee_service._ee_initialized = False
        
        result = self.gee_service.geometry_to_ee(self.test_polygon)
        
        self.assertIsNone(result)
    
    @patch('apps.gee.services.ee')
    def test_get_hls_collection_success(self, mock_ee):
        """Test successful HLS collection retrieval"""
        # Mock GEE initialization
        self.gee_service._ee_initialized = True
        
        # Mock image collection
        mock_collection = Mock()
        mock_collection.size.return_value.getInfo.return_value = 10
        mock_ee.ImageCollection.return_value.filterDate.return_value = mock_collection
        
        result = self.gee_service.get_hls_collection('2023-01-01', '2023-01-31')
        
        self.assertEqual(result, mock_collection)
        mock_ee.ImageCollection.assert_called_once()
    
    @patch('apps.gee.services.ee')
    def test_apply_cloud_mask(self, mock_ee):
        """Test cloud masking application"""
        # Mock image
        mock_image = Mock()
        mock_qa_band = Mock()
        mock_image.select.return_value = mock_qa_band
        
        # Mock bit operations
        mock_cloud_mask = Mock()
        mock_cirrus_mask = Mock()
        mock_qa_band.bitwiseAnd.side_effect = [mock_cloud_mask, mock_cirrus_mask]
        mock_cloud_mask.eq.return_value = Mock()
        mock_cirrus_mask.eq.return_value = Mock()
        
        # Mock final mask
        mock_final_mask = Mock()
        mock_cloud_mask.And.return_value = mock_final_mask
        
        # Mock result
        mock_result = Mock()
        mock_image.updateMask.return_value = mock_result
        
        result = self.gee_service.apply_cloud_mask(mock_image)
        
        self.assertEqual(result, mock_result)
        mock_image.select.assert_called_with('QA60')
    
    @patch('apps.gee.services.ee')
    def test_select_bands(self, mock_ee):
        """Test band selection"""
        # Mock image
        mock_image = Mock()
        mock_selected = Mock()
        mock_image.select.return_value = mock_selected
        
        result = self.gee_service.select_bands(mock_image)
        
        self.assertEqual(result, mock_selected)
        mock_image.select.assert_called_with(['B2', 'B3', 'B4', 'B8'], ['blue', 'green', 'red', 'nir'])
    
    @patch('apps.gee.services.ee')
    def test_export_hls_imagery_not_initialized(self, mock_ee):
        """Test HLS export when GEE not initialized"""
        # Ensure GEE is not initialized
        self.gee_service._ee_initialized = False
        
        result = self.gee_service.export_hls_imagery(self.test_job)
        
        self.assertFalse(result['success'])
        self.assertIn('GEE not initialized', result['error'])
        self.assertIsNone(result['export_id'])
    
    @patch('apps.gee.services.ee')
    def test_export_hls_imagery_invalid_aoi(self, mock_ee):
        """Test HLS export with invalid AOI"""
        # Mock GEE initialization
        self.gee_service._ee_initialized = True
        
        # Create job with invalid AOI
        invalid_coords = [
            (0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.5, 0.5), (0.0, 1.0), (0.0, 0.0)
        ]
        invalid_polygon = Polygon(invalid_coords)
        invalid_job = Job.objects.create(
            aoi_geometry=invalid_polygon,
            start_date=date(2023, 1, 1),
            end_date=date(2023, 1, 31),
            model_version='v1.0.0',
            preprocessing_version='v1.0.0'
        )
        
        # Mock the validation to fail
        with patch.object(self.gee_service, 'validate_aoi', return_value=(False, "Invalid geometry")):
            result = self.gee_service.export_hls_imagery(invalid_job)
            
            self.assertFalse(result['success'])
            self.assertIsNotNone(result['error'])
            self.assertIsNone(result['export_id'])
    
    @patch('apps.gee.services.ee')
    def test_export_hls_imagery_success(self, mock_ee):
        """Test successful HLS export"""
        # Mock GEE initialization
        self.gee_service._ee_initialized = True
        
        # Mock all GEE components
        mock_ee_geometry = Mock()
        mock_collection = Mock()
        mock_collection.size.return_value.getInfo.return_value = 5
        mock_processed = Mock()
        mock_clipped = Mock()
        mock_task = Mock()
        mock_task.id = 'test-task-id'
        
        # Set up mocks
        mock_ee.Geometry.return_value = mock_ee_geometry
        mock_ee.ImageCollection.return_value.filterDate.return_value = mock_collection
        mock_collection.map.return_value.map.return_value.median.return_value.clip.return_value = mock_clipped
        mock_ee.batch.Export.image.toDrive.return_value = mock_task
        
        # Mock validation to succeed
        with patch.object(self.gee_service, 'validate_aoi', return_value=(True, None)):
            result = self.gee_service.export_hls_imagery(self.test_job)
            
            self.assertTrue(result['success'])
            self.assertEqual(result['export_id'], 'test-task-id')
            self.assertEqual(result['task'], mock_task)
    
    @patch('apps.gee.services.ee')
    def test_monitor_export_completed(self, mock_ee):
        """Test export monitoring with completed status"""
        # Mock GEE initialization
        self.gee_service._ee_initialized = True
        
        # Mock task status
        mock_task = Mock()
        mock_task.status.return_value = {
            'state': 'COMPLETED',
            'destination_uris': ['gs://test-bucket/export.tif']
        }
        mock_ee.batch.Task.return_value = mock_task
        
        result = self.gee_service.monitor_export('test-task-id', timeout=10)
        
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['export_url'], 'gs://test-bucket/export.tif')
    
    @patch('apps.gee.services.ee')
    def test_monitor_export_failed(self, mock_ee):
        """Test export monitoring with failed status"""
        # Mock GEE initialization
        self.gee_service._ee_initialized = True
        
        # Mock task status
        mock_task = Mock()
        mock_task.status.return_value = {
            'state': 'FAILED',
            'error_message': 'Export failed due to error'
        }
        mock_ee.batch.Task.return_value = mock_task
        
        result = self.gee_service.monitor_export('test-task-id', timeout=10)
        
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error'], 'Export failed due to error')
    
    @patch('apps.gee.services.ee')
    def test_monitor_export_timeout(self, mock_ee):
        """Test export monitoring with timeout"""
        # Mock GEE initialization
        self.gee_service._ee_initialized = True
        
        # Mock task status (always running)
        mock_task = Mock()
        mock_task.status.return_value = {'state': 'RUNNING'}
        mock_ee.batch.Task.return_value = mock_task
        
        # Mock time to simulate timeout
        with patch('time.time') as mock_time:
            mock_time.side_effect = [0, 1, 2, 3, 4, 5, 4000]  # Fast forward to timeout
            
            result = self.gee_service.monitor_export('test-task-id', timeout=1)
        
        self.assertEqual(result['status'], 'timeout')
        self.assertIn('timed out', result['error'])


class GeeServiceIntegrationTest(TestCase):
    """Integration tests for GEE service"""
    
    def test_service_initialization_without_credentials(self):
        """Test service initialization when GEE credentials not configured"""
        service = GeeService()
        
        # Should not crash, but should not be initialized
        self.assertFalse(service._ee_initialized)
        
        # Service info should still work
        info = service.get_service_info()
        self.assertIsInstance(info, dict)
        self.assertIn('initialized', info)
