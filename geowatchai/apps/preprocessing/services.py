"""
Preprocessing Service for GeoTIFF imagery from GEE exports.

This service handles:
- Loading GeoTIFF imagery exported by GEE
- Extracting specific spectral bands (B3, B4, B8, B11, B12)
- Calculating Baresoil Index (BSI)
- Stacking bands into 6-channel tensor for model input
- Normalization to [0, 1] range and float32 conversion
"""

import logging
import numpy as np
import rasterio
from rasterio.enums import Resampling
from typing import Tuple, Optional, Dict, Any
import torch
from pathlib import Path
import os

logger = logging.getLogger(__name__)


class PreprocessingService:
    """
    Service for preprocessing GEE-exported GeoTIFF imagery into model-ready tensors.
    
    This service extracts specific bands from HLS imagery, calculates spectral indices,
    and prepares the data in the format expected by the ML model.
    """
    
    # HLS band mapping for Sentinel-2
    # B2=Blue, B3=Green, B4=Red, B8=NIR, B11=SWIR1, B12=SWIR2
    HLS_BAND_MAPPING = {
        'B2': 1,  # Blue - needed for BSI calculation
        'B3': 2,  # Green
        'B4': 3,  # Red  
        'B8': 4,  # NIR
        'B11': 5, # SWIR1
        'B12': 6  # SWIR2
    }
    
    # Model input band order: [Green, Red, NIR, SWIR1, SWIR2, BSI]
    MODEL_BAND_ORDER = ['B3', 'B4', 'B8', 'B11', 'B12', 'BSI']
    
    def __init__(self):
        """Initialize the preprocessing service."""
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
    
    def load_geotiff(self, geotiff_path: str) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Load GeoTIFF file and return data array with metadata.
        
        Args:
            geotiff_path: Path to the GeoTIFF file
            
        Returns:
            Tuple of (data_array, metadata_dict)
            
        Raises:
            FileNotFoundError: If the GeoTIFF file doesn't exist
            ValueError: If the file cannot be read or is invalid
        """
        if not os.path.exists(geotiff_path):
            raise FileNotFoundError(f"GeoTIFF file not found: {geotiff_path}")
        
        try:
            with rasterio.open(geotiff_path) as src:
                # Read all bands
                data = src.read()
                metadata = {
                    'crs': src.crs,
                    'transform': src.transform,
                    'width': src.width,
                    'height': src.height,
                    'count': src.count,
                    'dtype': src.dtypes[0],
                    'nodata': src.nodata
                }
                
                self.logger.info(f"Loaded GeoTIFF: {geotiff_path}, shape: {data.shape}, bands: {src.count}")
                return data, metadata
                
        except Exception as e:
            raise ValueError(f"Failed to load GeoTIFF {geotiff_path}: {str(e)}")
    
    def extract_bands(self, data: np.ndarray) -> Dict[str, np.ndarray]:
        """
        Extract required bands from the loaded GeoTIFF data.
        
        Args:
            data: Multi-band array from GeoTIFF (bands, height, width)
            
        Returns:
            Dictionary mapping band names to their arrays
            
        Raises:
            ValueError: If required bands are not available
        """
        bands = {}
        
        # Extract required bands based on HLS band mapping
        for band_name, band_index in self.HLS_BAND_MAPPING.items():
            if band_index > data.shape[0]:
                raise ValueError(f"Band {band_name} (index {band_index}) not available in data with {data.shape[0]} bands")
            
            bands[band_name] = data[band_index - 1].copy()  # Convert to 0-based index
        
        self.logger.info(f"Extracted bands: {list(bands.keys())}")
        return bands
    
    def calculate_bsi(self, bands: Dict[str, np.ndarray]) -> np.ndarray:
        """
        Calculate Baresoil Index (BSI) using the formula:
        BSI = ((B11 + B4) - (B8 + B2)) / ((B11 + B4) + (B8 + B2))
        
        Args:
            bands: Dictionary containing band arrays
            
        Returns:
            BSI array with same spatial dimensions as input bands
        """
        try:
            b11 = bands['B11'].astype(np.float32)
            b4 = bands['B4'].astype(np.float32)
            b8 = bands['B8'].astype(np.float32)
            b2 = bands['B2'].astype(np.float32)
            
            # Calculate BSI: ((B11 + B4) - (B8 + B2)) / ((B11 + B4) + (B8 + B2))
            numerator = (b11 + b4) - (b8 + b2)
            denominator = (b11 + b4) + (b8 + b2)
            
            # Avoid division by zero
            denominator = np.where(denominator == 0, 1e-8, denominator)
            bsi = numerator / denominator
            
            # Clip to reasonable range [-1, 1] to handle outliers
            bsi = np.clip(bsi, -1, 1)
            
            self.logger.info(f"Calculated BSI, range: [{bsi.min():.3f}, {bsi.max():.3f}]")
            return bsi
            
        except KeyError as e:
            raise ValueError(f"Required band for BSI calculation not found: {str(e)}")
        except Exception as e:
            raise ValueError(f"Failed to calculate BSI: {str(e)}")
    
    def normalize_bands(self, bands: Dict[str, np.ndarray]) -> Dict[str, np.ndarray]:
        """
        Normalize band values to [0, 1] range.
        
        Args:
            bands: Dictionary of band arrays
            
        Returns:
            Dictionary with normalized band arrays
        """
        normalized_bands = {}
        
        for band_name, band_data in bands.items():
            if band_name == 'BSI':
                # BSI is already in [-1, 1] range, normalize to [0, 1]
                normalized = (band_data + 1) / 2
            else:
                # For reflectance bands, assume they are in 0-10000 range (common for HLS)
                # or use min-max normalization if range is different
                band_min = float(band_data.min())
                band_max = float(band_data.max())
                
                if band_max > band_min:
                    normalized = (band_data - band_min) / (band_max - band_min)
                else:
                    # If all values are the same, set to 0
                    normalized = np.zeros_like(band_data)
            
            # Ensure values are in [0, 1] range
            normalized = np.clip(normalized, 0, 1)
            normalized_bands[band_name] = normalized.astype(np.float32)
            
            self.logger.debug(f"Normalized {band_name}: range [{normalized.min():.3f}, {normalized.max():.3f}]")
        
        return normalized_bands
    
    def stack_bands(self, bands: Dict[str, np.ndarray]) -> np.ndarray:
        """
        Stack bands in the order expected by the model: [Green, Red, NIR, SWIR1, SWIR2, BSI].
        
        Args:
            bands: Dictionary containing band and BSI arrays
            
        Returns:
            Stacked array with shape (6, height, width)
        """
        stacked_bands = []
        
        for band_name in self.MODEL_BAND_ORDER:
            if band_name not in bands:
                raise ValueError(f"Required band/BSI '{band_name}' not available")
            
            stacked_bands.append(bands[band_name])
        
        # Stack into (channels, height, width) format
        stacked = np.stack(stacked_bands, axis=0)
        
        self.logger.info(f"Stacked bands into tensor shape: {stacked.shape}")
        return stacked.astype(np.float32)
    
    def preprocess_geotiff(self, geotiff_path: str) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Main preprocessing pipeline: load GeoTIFF, extract bands, calculate BSI, 
        normalize, and stack into model-ready tensor.
        
        Args:
            geotiff_path: Path to the GeoTIFF file from GEE export
            
        Returns:
            Tuple of (preprocessed_tensor, metadata)
            
        Raises:
            FileNotFoundError: If the GeoTIFF file doesn't exist
            ValueError: If preprocessing fails at any step
        """
        try:
            # Step 1: Load GeoTIFF
            self.logger.info(f"Starting preprocessing for: {geotiff_path}")
            data, metadata = self.load_geotiff(geotiff_path)

            # Diagnostic: log raw rasterio values before any processing
            _finite = data[np.isfinite(data)]
            self.logger.info(
                f"[Diag] Raw rasterio values — min={_finite.min():.4f}, max={_finite.max():.4f}, "
                f"mean={_finite.mean():.4f}, dtype={data.dtype}"
            )

            # Replace NaN/inf with 0 before band extraction and BSI calculation.
            # Matches test notebook exactly:
            # np.nan_to_num(patch_raw, nan=0.0, posinf=0.0, neginf=0.0)
            data = np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0)

            # Step 2: Extract required bands
            bands = self.extract_bands(data)
            
            # Step 3: Calculate BSI
            bsi = self.calculate_bsi(bands)
            bands['BSI'] = bsi

            # Step 4: Stack bands in model order — no normalization here.
            # Per-band p2-p98 percentile normalization is applied per 256×256
            # tile inside InferenceService.predict_tiled(), matching exactly
            # how the training patches were generated.
            tensor = self.stack_bands(bands)
            
            # Add preprocessing metadata
            metadata.update({
                'preprocessing_applied': True,
                'bands_extracted': list(self.HLS_BAND_MAPPING.keys()),
                'bsi_calculated': True,
                'tensor_shape': tensor.shape,
                'tensor_dtype': str(tensor.dtype),
                'value_range': '[0, 1]',
                'band_order': self.MODEL_BAND_ORDER
            })
            
            self.logger.info(f"Preprocessing completed successfully. Tensor shape: {tensor.shape}")
            return tensor, metadata
            
        except Exception as e:
            self.logger.error(f"Preprocessing failed for {geotiff_path}: {str(e)}")
            raise
    
    def tensor_to_pytorch(self, tensor: np.ndarray) -> torch.Tensor:
        """
        Convert numpy tensor to PyTorch tensor.
        
        Args:
            tensor: Numpy array with shape (channels, height, width)
            
        Returns:
            PyTorch tensor with same shape
        """
        return torch.from_numpy(tensor)
    
    def validate_tensor(self, tensor: np.ndarray) -> bool:
        """
        Validate that the tensor meets model requirements.
        
        Args:
            tensor: Preprocessed tensor to validate
            
        Returns:
            True if valid, raises ValueError if invalid
        """
        # Check for NaN or infinite values
        if np.isnan(tensor).any():
            raise ValueError("Tensor contains NaN values")

        if np.isinf(tensor).any():
            raise ValueError("Tensor contains infinite values")
        
        # Check shape (should be 6 channels)
        if tensor.shape[0] != 6:
            raise ValueError(f"Expected 6 channels, got {tensor.shape[0]}")
        
        # Check data type
        if tensor.dtype != np.float32:
            raise ValueError(f"Expected float32 dtype, got {tensor.dtype}")
        
        # Value range is intentionally NOT checked here.
        # HLS reflectance bands are ~[0, 1] but BSI spans [-1, 1].
        # Per-tile p2-p98 normalization in InferenceService maps everything
        # to [0, 1] before it reaches the model.
        
        self.logger.info("Tensor validation passed")
        return True


# Singleton instance for the service
_preprocessing_service = None


def get_preprocessing_service() -> PreprocessingService:
    """
    Get singleton instance of the preprocessing service.
    
    Returns:
        PreprocessingService instance
    """
    global _preprocessing_service
    if _preprocessing_service is None:
        _preprocessing_service = PreprocessingService()
    return _preprocessing_service