"""
Unit tests for Preprocessing Service.

Tests cover:
- GeoTIFF loading and validation
- Band extraction from HLS data
- BSI calculation with correct formula
- Band normalization to [0, 1] range
- Tensor stacking in correct order
- Error handling and edge cases
"""

import pytest
import numpy as np
import rasterio
from rasterio.transform import from_bounds
import tempfile
import os
from unittest.mock import patch, MagicMock
from pathlib import Path
import torch  
from apps.preprocessing.services import PreprocessingService, get_preprocessing_service


class TestPreprocessingService:
    """Test cases for PreprocessingService class."""
    
    def setup_method(self):
        """Set up test fixtures before each test method."""
        self.service = PreprocessingService()
        self.temp_dir = tempfile.mkdtemp()
        
        # Create test data with 6 bands (B2, B3, B4, B8, B11, B12)
        self.height = 100
        self.width = 100
        self.num_bands = 6
        
        # Generate realistic HLS band values
        np.random.seed(42)  # For reproducible tests
        self.test_data = np.random.randint(0, 10000, size=(self.num_bands, self.height, self.width), dtype=np.uint16)
        
        # Create a temporary GeoTIFF file
        self.geotiff_path = os.path.join(self.temp_dir, 'test_hls.tif')
        self.create_test_geotiff()
    
    def teardown_method(self):
        """Clean up after each test method."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def create_test_geotiff(self):
        """Create a test GeoTIFF file with HLS band data."""
        # Define CRS and transform
        crs = 'EPSG:4326'
        transform = from_bounds(-180, -90, 180, 90, self.width, self.height)
        
        # Create GeoTIFF
        with rasterio.open(
            self.geotiff_path,
            'w',
            driver='GTiff',
            height=self.height,
            width=self.width,
            count=self.num_bands,
            dtype=self.test_data.dtype,
            crs=crs,
            transform=transform
        ) as dst:
            for i in range(self.num_bands):
                dst.write(self.test_data[i], i + 1)
    
    def test_service_initialization(self):
        """Test that the service initializes correctly."""
        assert self.service is not None
        assert hasattr(self.service, 'HLS_BAND_MAPPING')
        assert hasattr(self.service, 'MODEL_BAND_ORDER')
        assert len(self.service.MODEL_BAND_ORDER) == 6
        assert self.service.MODEL_BAND_ORDER == ['B3', 'B4', 'B8', 'B11', 'B12', 'BSI']
    
    def test_load_geotiff_success(self):
        """Test successful GeoTIFF loading."""
        data, metadata = self.service.load_geotiff(self.geotiff_path)
        
        assert data.shape == (self.num_bands, self.height, self.width)
        assert 'crs' in metadata
        assert 'transform' in metadata
        assert 'width' in metadata
        assert 'height' in metadata
        assert 'count' in metadata
        assert metadata['count'] == self.num_bands
    
    def test_load_geotiff_file_not_found(self):
        """Test loading non-existent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="GeoTIFF file not found"):
            self.service.load_geotiff('/nonexistent/path.tif')
    
    def test_load_geotiff_invalid_file(self):
        """Test loading invalid file raises ValueError."""
        invalid_path = os.path.join(self.temp_dir, 'invalid.txt')
        with open(invalid_path, 'w') as f:
            f.write('not a geotiff')
        
        with pytest.raises(ValueError, match="Failed to load GeoTIFF"):
            self.service.load_geotiff(invalid_path)
    
    def test_extract_bands_success(self):
        """Test successful band extraction."""
        data, _ = self.service.load_geotiff(self.geotiff_path)
        bands = self.service.extract_bands(data)
        
        expected_bands = ['B2', 'B3', 'B4', 'B8', 'B11', 'B12']
        assert len(bands) == len(expected_bands)
        for band_name in expected_bands:
            assert band_name in bands
            assert bands[band_name].shape == (self.height, self.width)
    
    def test_extract_bands_insufficient_bands(self):
        """Test extraction fails when data has insufficient bands."""
        # Create data with only 3 bands
        insufficient_data = np.random.randint(0, 10000, size=(3, 50, 50), dtype=np.uint16)
        
        with pytest.raises(ValueError, match="Band B8 .* not available"):
            self.service.extract_bands(insufficient_data)
    
    def test_calculate_bsi_success(self):
        """Test successful BSI calculation."""
        # Create test bands with known values
        bands = {
            'B2': np.full((10, 10), 1000, dtype=np.float32),  # Blue
            'B4': np.full((10, 10), 2000, dtype=np.float32),  # Red
            'B8': np.full((10, 10), 3000, dtype=np.float32),  # NIR
            'B11': np.full((10, 10), 4000, dtype=np.float32)  # SWIR1
        }
        
        bsi = self.service.calculate_bsi(bands)
        
        # Expected BSI: ((4000 + 2000) - (3000 + 1000)) / ((4000 + 2000) + (3000 + 1000))
        # = (6000 - 4000) / (6000 + 4000) = 2000 / 10000 = 0.2
        expected_bsi = np.full((10, 10), 0.2, dtype=np.float32)
        
        np.testing.assert_array_almost_equal(bsi, expected_bsi, decimal=5)
        assert bsi.shape == (10, 10)
        assert bsi.dtype == np.float32
    
    def test_calculate_bsi_missing_band(self):
        """Test BSI calculation fails when required band is missing."""
        bands = {
            'B2': np.full((10, 10), 1000, dtype=np.float32),
            'B4': np.full((10, 10), 2000, dtype=np.float32),
            # Missing B8 and B11
        }
        
        with pytest.raises(ValueError, match="Required band for BSI calculation not found"):
            self.service.calculate_bsi(bands)
    
    def test_calculate_bsi_division_by_zero(self):
        """Test BSI calculation handles division by zero gracefully."""
        # Create bands that will result in zero denominator
        bands = {
            'B2': np.full((10, 10), 1000, dtype=np.float32),
            'B4': np.full((10, 10), -1000, dtype=np.float32),  # Negative to cancel out
            'B8': np.full((10, 10), 1000, dtype=np.float32),
            'B11': np.full((10, 10), -1000, dtype=np.float32)  # Negative to cancel out
        }
        
        bsi = self.service.calculate_bsi(bands)
        
        # Should not raise error and should handle zero denominator
        assert bsi.shape == (10, 10)
        assert not np.isnan(bsi).any()
        assert not np.isinf(bsi).any()
    
    def test_normalize_bands_reflectance(self):
        """Test normalization of reflectance bands."""
        bands = {
            'B3': np.array([[1000, 2000], [3000, 4000]], dtype=np.float32),
            'B4': np.array([[500, 1500], [2500, 3500]], dtype=np.float32)
        }
        
        normalized = self.service.normalize_bands(bands)
        
        # Check that values are in [0, 1] range
        for band_name, band_data in normalized.items():
            assert band_data.min() >= 0
            assert band_data.max() <= 1
            assert band_data.dtype == np.float32
        
        # Check specific normalization for B3: (x - 1000) / (4000 - 1000)
        expected_b3 = np.array([[0.0, 0.33333333], [0.66666667, 1.0]], dtype=np.float32)
        np.testing.assert_array_almost_equal(normalized['B3'], expected_b3, decimal=5)
    
    def test_normalize_bands_bsi(self):
        """Test normalization of BSI from [-1, 1] to [0, 1]."""
        bands = {
            'BSI': np.array([[-1.0, -0.5, 0.0], [0.5, 1.0, 0.2]], dtype=np.float32)
        }
        
        normalized = self.service.normalize_bands(bands)
        
        # BSI normalization: (x + 1) / 2
        expected = np.array([[0.0, 0.25, 0.5], [0.75, 1.0, 0.6]], dtype=np.float32)
        np.testing.assert_array_almost_equal(normalized['BSI'], expected, decimal=5)
    
    def test_normalize_bands_constant_values(self):
        """Test normalization when all values are the same."""
        bands = {
            'B3': np.full((5, 5), 2000, dtype=np.float32)
        }
        
        normalized = self.service.normalize_bands(bands)
        
        # Should be all zeros when all input values are the same
        expected = np.zeros((5, 5), dtype=np.float32)
        np.testing.assert_array_equal(normalized['B3'], expected)
    
    def test_stack_bands_success(self):
        """Test successful band stacking in correct order."""
        # Create test bands
        bands = {}
        for band_name in self.service.MODEL_BAND_ORDER:
            bands[band_name] = np.random.rand(10, 10).astype(np.float32)
        
        stacked = self.service.stack_bands(bands)
        
        assert stacked.shape == (6, 10, 10)
        assert stacked.dtype == np.float32
        
        # Verify order by checking first band (should be B3)
        np.testing.assert_array_equal(stacked[0], bands['B3'])
        # Verify last band (should be BSI)
        np.testing.assert_array_equal(stacked[5], bands['BSI'])
    
    def test_stack_bands_missing_band(self):
        """Test band stacking fails when required band is missing."""
        bands = {
            'B3': np.random.rand(10, 10).astype(np.float32),
            'B4': np.random.rand(10, 10).astype(np.float32),
            # Missing other bands
        }
        
        with pytest.raises(ValueError, match="Required band/BSI 'B8' not available"):
            self.service.stack_bands(bands)
    
    def test_preprocess_geotiff_success(self):
        """Test complete preprocessing pipeline."""
        tensor, metadata = self.service.preprocess_geotiff(self.geotiff_path)
        
        # Check tensor properties
        assert tensor.shape == (6, self.height, self.width)
        assert tensor.dtype == np.float32
        assert tensor.min() >= 0
        assert tensor.max() <= 1
        
        # Check metadata
        assert metadata['preprocessing_applied'] is True
        assert metadata['bsi_calculated'] is True
        assert metadata['tensor_shape'] == tensor.shape
        assert metadata['tensor_dtype'] == 'float32'
        assert metadata['value_range'] == '[0, 1]'
        assert metadata['band_order'] == self.service.MODEL_BAND_ORDER
    
    def test_preprocess_geotiff_file_not_found(self):
        """Test preprocessing fails when file doesn't exist."""
        with pytest.raises(FileNotFoundError, match="GeoTIFF file not found"):
            self.service.preprocess_geotiff('/nonexistent/file.tif')
    
    def test_tensor_to_pytorch(self):
        """Test conversion of numpy tensor to PyTorch tensor."""
        numpy_tensor = np.random.rand(6, 10, 10).astype(np.float32)
        pytorch_tensor = self.service.tensor_to_pytorch(numpy_tensor)
        
        assert isinstance(pytorch_tensor, torch.Tensor)
        assert pytorch_tensor.shape == numpy_tensor.shape
        assert pytorch_tensor.dtype == torch.float32
        np.testing.assert_array_equal(pytorch_tensor.numpy(), numpy_tensor)
    
    def test_validate_tensor_success(self):
        """Test validation of correct tensor."""
        tensor = np.random.rand(6, 10, 10).astype(np.float32)
        tensor = np.clip(tensor, 0, 1)  # Ensure values are in [0, 1]
        
        assert self.service.validate_tensor(tensor) is True
    
    def test_validate_tensor_wrong_channels(self):
        """Test validation fails with wrong number of channels."""
        tensor = np.random.rand(5, 10, 10).astype(np.float32)  # 5 channels instead of 6
        
        with pytest.raises(ValueError, match="Expected 6 channels, got 5"):
            self.service.validate_tensor(tensor)
    
    def test_validate_tensor_wrong_dtype(self):
        """Test validation fails with wrong data type."""
        tensor = np.random.rand(6, 10, 10).astype(np.float64)  # float64 instead of float32
        
        with pytest.raises(ValueError, match="Expected float32 dtype, got float64"):
            self.service.validate_tensor(tensor)
    
    def test_validate_tensor_out_of_range(self):
        """Test validation fails with values outside [0, 1] range."""
        tensor = np.random.rand(6, 10, 10).astype(np.float32)
        tensor[0, 0, 0] = 1.5  # Set value outside range
        
        with pytest.raises(ValueError, match="Tensor values outside .* range"):
            self.service.validate_tensor(tensor)
    
    def test_validate_tensor_nan_values(self):
        """Test validation fails with NaN values."""
        tensor = np.random.rand(6, 10, 10).astype(np.float32)
        tensor[0, 0, 0] = np.nan
        
        with pytest.raises(ValueError, match="Tensor contains NaN values"):
            self.service.validate_tensor(tensor)
    
    def test_validate_tensor_inf_values(self):
        """Test validation fails with infinite values."""
        tensor = np.random.rand(6, 10, 10).astype(np.float32)
        tensor[0, 0, 0] = np.inf
        
        with pytest.raises(ValueError, match="Tensor contains infinite values"):
            self.service.validate_tensor(tensor)
    
    @patch('apps.preprocessing.services.rasterio.open')
    def test_load_geotiff_rasterio_error(self, mock_open):
        """Test handling of rasterio errors during GeoTIFF loading."""
        mock_open.side_effect = Exception("Rasterio error")
        
        with pytest.raises(ValueError, match="Failed to load GeoTIFF"):
            self.service.load_geotiff(self.geotiff_path)


class TestPreprocessingServiceSingleton:
    """Test cases for the preprocessing service singleton pattern."""
    
    def test_get_preprocessing_service_singleton(self):
        """Test that get_preprocessing_service returns the same instance."""
        service1 = get_preprocessing_service()
        service2 = get_preprocessing_service()
        
        assert service1 is service2
        assert isinstance(service1, PreprocessingService)
    
    def test_get_preprocessing_service_initializes_once(self):
        """Test that the service is initialized only once."""
        # Clear the singleton instance
        import apps.preprocessing.services
        apps.preprocessing.services._preprocessing_service = None
        
        service1 = get_preprocessing_service()
        service2 = get_preprocessing_service()
        
        assert service1 is service2


class TestPreprocessingServiceIntegration:
    """Integration tests for the preprocessing service."""
    
    def setup_method(self):
        """Set up integration test fixtures."""
        self.service = PreprocessingService()
        self.temp_dir = tempfile.mkdtemp()
    
    def teardown_method(self):
        """Clean up after integration tests."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_full_pipeline_with_realistic_data(self):
        """Test the full preprocessing pipeline with realistic HLS data."""
        # Create realistic HLS data
        height, width = 64, 64
        num_bands = 6
        
        # Simulate realistic HLS reflectance values (0-10000)
        np.random.seed(123)
        data = np.random.randint(0, 10000, size=(num_bands, height, width), dtype=np.uint16)
        
        # Create GeoTIFF
        geotiff_path = os.path.join(self.temp_dir, 'realistic_hls.tif')
        crs = 'EPSG:4326'
        transform = from_bounds(-180, -90, 180, 90, width, height)
        
        with rasterio.open(
            geotiff_path,
            'w',
            driver='GTiff',
            height=height,
            width=width,
            count=num_bands,
            dtype=data.dtype,
            crs=crs,
            transform=transform
        ) as dst:
            for i in range(num_bands):
                dst.write(data[i], i + 1)
        
        # Run full preprocessing
        tensor, metadata = self.service.preprocess_geotiff(geotiff_path)
        
        # Validate results
        assert tensor.shape == (6, height, width)
        assert tensor.dtype == np.float32
        assert 0 <= tensor.min() <= tensor.max() <= 1
        
        # Validate tensor
        self.service.validate_tensor(tensor)
        
        # Convert to PyTorch
        pytorch_tensor = self.service.tensor_to_pytorch(tensor)
        assert isinstance(pytorch_tensor, torch.Tensor)
        assert pytorch_tensor.shape == tensor.shape
