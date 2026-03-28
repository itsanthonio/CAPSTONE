import logging
import os
import threading
from typing import Optional, Tuple, List
import numpy as np
import torch
import torch.nn as nn
import segmentation_models_pytorch as smp
from pathlib import Path

logger = logging.getLogger(__name__)


class ModelSingleton:
    """Singleton pattern for ML model loading to prevent memory overflow"""

    _instance = None
    _lock = threading.Lock()
    _model = None
    _device = None

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._model is None:
            self._load_model()

    def _load_model(self):
        """Load PyTorch model once per worker using exact architecture from training notebook"""
        try:
            # Model path — resolve relative paths against the Django BASE_DIR
            raw_path = os.getenv('MODEL_PATH', 'models/best_precision_model_6band.pth')
            model_path = Path(raw_path)
            if not model_path.is_absolute():
                from django.conf import settings
                model_path = Path(settings.BASE_DIR) / model_path
            model_path = str(model_path)

            if not os.path.exists(model_path):
                logger.warning(f"Model file not found at {model_path}, using mock model")
                self._model = self._create_mock_model()
                self._device = torch.device('cpu')
            else:
                logger.info(f"Loading model from {model_path}")

                # Initialize model with exact architecture from training notebook
                self._model = smp.FPN(
                    encoder_name='resnet50',
                    encoder_weights=None,  # No pretrained weights since loading custom
                    in_channels=6,  # 6 channels from preprocessing service
                    classes=1,  # Binary classification
                    activation=None  # No activation, will apply sigmoid manually
                )

                # Set device first (prioritize CUDA for high-speed detection)
                self._device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
                self._model = self._model.to(self._device)

                # The checkpoint may contain pathlib.PosixPath objects
                # (saved on Linux/Mac).  On Windows, PosixPath exists but
                # cannot be instantiated, so we shim it to WindowsPath for
                # the duration of the load.  On Linux/Mac the shim is a no-op.
                import pathlib as _pathlib
                import platform as _platform
                _orig_posix = _pathlib.PosixPath
                if _platform.system() == 'Windows':
                    _pathlib.PosixPath = _pathlib.WindowsPath
                try:
                    checkpoint = torch.load(
                        model_path, map_location=self._device, weights_only=False
                    )
                finally:
                    _pathlib.PosixPath = _orig_posix

                # Load model state dict
                self._model.load_state_dict(checkpoint['model_state_dict'])
                self._model.eval()  # Set to evaluation mode

            logger.info(f"Model loaded successfully on device: {self._device}")
            logger.info(f"Model architecture: FPN with ResNet50 encoder")
            logger.info(f"Model parameters: {sum(p.numel() for p in self._model.parameters()):,}")

        except Exception as e:
            logger.error(f"Failed to load model: {str(e)}")
            self._model = self._create_mock_model()
            self._device = torch.device('cpu')

    def _create_mock_model(self) -> nn.Module:
        """Create a mock FPN model for testing when real model is not available"""

        class MockFPNModel(nn.Module):
            def __init__(self):
                super().__init__()
                # Simple mock that mimics FPN output structure
                self.conv = nn.Conv2d(6, 1, kernel_size=1)  # 6 channels to 1

            def forward(self, x):
                return self.conv(x)

        return MockFPNModel()

    @property
    def model(self) -> nn.Module:
        """Get the loaded model"""
        return self._model

    @property
    def device(self) -> torch.device:
        """Get the device the model is loaded on"""
        return self._device


class InferenceService:
    """Business logic for model inference following Anti-Vibe guardrails"""

    def __init__(self):
        self.model_singleton = ModelSingleton()
        self.model = self.model_singleton.model
        self.device = self.model_singleton.device

    def predict(self, preprocessed_tensor: np.ndarray) -> np.ndarray:
        """
        Run inference on preprocessed 6-channel tensor from PreprocessingService

        Args:
            preprocessed_tensor: 6-channel tensor with shape (6, H, W) from preprocessing

        Returns:
            np.ndarray: Probability mask with shape (H, W) in range [0, 1]

        Raises:
            ValueError: If input validation fails
        """
        if preprocessed_tensor is None:
            raise ValueError("No preprocessed tensor provided for inference")

        try:
            # Validate input shape (should be 6-channel from preprocessing)
            if len(preprocessed_tensor.shape) != 3 or preprocessed_tensor.shape[0] != 6:
                raise ValueError(f"Expected 6-channel tensor with shape (6, H, W), got {preprocessed_tensor.shape}")

            # Convert to tensor and add batch dimension
            if not isinstance(preprocessed_tensor, torch.Tensor):
                tensor = torch.from_numpy(preprocessed_tensor).float()
            else:
                tensor = preprocessed_tensor.float()

            # Add batch dimension: (6, H, W) -> (1, 6, H, W)
            tensor = tensor.unsqueeze(0)

            # Pad to nearest multiple of 32 (required by FPN encoder)
            _, _, h, w = tensor.shape
            pad_h = (32 - h % 32) % 32
            pad_w = (32 - w % 32) % 32
            if pad_h > 0 or pad_w > 0:
                import torch.nn.functional as F
                tensor = F.pad(tensor, (0, pad_w, 0, pad_h), mode='reflect')

            # Move to device
            tensor = tensor.to(self.device)

            # Run inference with torch.no_grad() for efficiency
            with torch.no_grad():
                # Forward pass through model
                raw_output = self.model(tensor)

                # Apply sigmoid activation to get probabilities [0, 1]
                probabilities = torch.sigmoid(raw_output)

                # Crop back to original size before padding
                probability_mask = probabilities.squeeze(0).cpu().numpy()
                if pad_h > 0 or pad_w > 0:
                    probability_mask = probability_mask[:, :h, :w]

            logger.info(f"Inference completed for tensor shape {preprocessed_tensor.shape}")
            logger.debug(f"Output probability range: [{probability_mask.min():.3f}, {probability_mask.max():.3f}]")

            return probability_mask

        except Exception as e:
            logger.error(f"Inference failed: {str(e)}")
            raise

    def predict_batch(self, preprocessed_tensors: List[np.ndarray]) -> List[np.ndarray]:
        """
        Run inference on a batch of preprocessed 6-channel tensors

        Args:
            preprocessed_tensors: List of 6-channel tensors from preprocessing

        Returns:
            List[np.ndarray]: List of probability masks
        """
        if not preprocessed_tensors:
            raise ValueError("No preprocessed tensors provided for inference")

        try:
            # Convert to tensor batch
            batch_tensors = []
            for tensor in preprocessed_tensors:
                if not isinstance(tensor, torch.Tensor):
                    tensor = torch.from_numpy(tensor).float()
                batch_tensors.append(tensor)

            # Create batch: List[(6, H, W)] -> (batch_size, 6, H, W)
            batch = torch.stack(batch_tensors).to(self.device)

            # Run inference
            with torch.no_grad():
                raw_output = self.model(batch)
                probabilities = torch.sigmoid(raw_output)

                # Convert back to list of numpy arrays
                results = []
                for i in range(probabilities.shape[0]):
                    probability_mask = probabilities[i].cpu().numpy()
                    results.append(probability_mask)

            logger.info(f"Processed batch of {len(preprocessed_tensors)} tensors")
            return results

        except Exception as e:
            logger.error(f"Batch inference failed: {str(e)}")
            raise

    def predict_single(self, preprocessed_tensor: np.ndarray) -> np.ndarray:
        """
        Run inference on a single preprocessed tensor

        Args:
            preprocessed_tensor: Single 6-channel tensor from preprocessing

        Returns:
            np.ndarray: Probability mask
        """
        return self.predict(preprocessed_tensor)

    def predict_tiled(self, preprocessed_tensor: np.ndarray,
                      tile_size: int = 256, overlap: int = 128) -> np.ndarray:
        """
        Run tiled inference on an arbitrary-sized tensor.

        Splits the input into overlapping tile_size×tile_size tiles, runs
        inference on each, and blends overlapping regions using a linear-ramp
        weight window.  Returns a stitched (H, W) probability mask in [0, 1]
        with the same spatial extent as the input — all downstream postprocessing
        (polygon extraction, hotspot coords, timelapse, Google Maps) is unaffected.

        Tiles smaller than tile_size (edge tiles) are padded with reflect-mode
        padding before inference, then cropped back, so the 30 m/pixel resolution
        is always preserved.

        Args:
            preprocessed_tensor: shape (6, H, W) float32 from PreprocessingService
            tile_size: patch size the model was trained on (default 256)
            overlap: overlap in pixels between adjacent tiles (default 128 = 50%)

        Returns:
            np.ndarray: stitched probability mask shape (H, W) in [0, 1]
        """
        if preprocessed_tensor is None:
            raise ValueError("No preprocessed tensor provided for tiled inference")
        if len(preprocessed_tensor.shape) != 3 or preprocessed_tensor.shape[0] != 6:
            raise ValueError(
                f"Expected (6, H, W) tensor, got {preprocessed_tensor.shape}"
            )

        _, H, W = preprocessed_tensor.shape
        stride = tile_size - overlap

        # Accumulation buffers for blended output
        output  = np.zeros((H, W), dtype=np.float32)
        weights = np.zeros((H, W), dtype=np.float32)

        # Linear-ramp weight window — centre pixels weighted highest, edges lowest
        weight_window = self._make_weight_window(tile_size)

        def _tile_starts(dim: int):
            """Return tile top-left positions along one dimension."""
            if dim <= tile_size:
                return [0]
            starts = list(range(0, dim - tile_size, stride))
            last = dim - tile_size
            if not starts or starts[-1] < last:
                starts.append(last)
            return starts

        row_starts = _tile_starts(H)
        col_starts = _tile_starts(W)

        logger.info(
            f"[Tiling] Input {H}×{W} px, tile={tile_size}, overlap={overlap}, "
            f"grid={len(row_starts)}×{len(col_starts)} "
            f"({len(row_starts) * len(col_starts)} tiles)"
        )

        for r0 in row_starts:
            for c0 in col_starts:
                r1 = min(r0 + tile_size, H)
                c1 = min(c0 + tile_size, W)
                tile_h = r1 - r0
                tile_w = c1 - c0

                tile = preprocessed_tensor[:, r0:r1, c0:c1]

                # Pad edge tiles to exactly tile_size × tile_size.
                # reflect mode reuses real pixel values so the model never
                # sees artificial zeros that could create false positives.
                if tile_h < tile_size or tile_w < tile_size:
                    tile = np.pad(
                        tile,
                        ((0, 0), (0, tile_size - tile_h), (0, tile_size - tile_w)),
                        mode='reflect',
                    )

                # Per-band p2-p98 percentile normalization — must happen per
                # tile so each patch is normalized exactly as it was at
                # training data generation time.
                tile = self._normalize_tile(tile)

                # Single-tile inference → (1, tile_size, tile_size)
                prob = self.predict(tile)

                # Strip the channel dim → (tile_size, tile_size)
                if prob.ndim == 3:
                    prob = prob.squeeze(0)

                # Crop back to actual tile dimensions for edge tiles
                prob = prob[:tile_h, :tile_w]
                w    = weight_window[:tile_h, :tile_w]

                output [r0:r1, c0:c1] += prob * w
                weights[r0:r1, c0:c1] += w

        # Normalise by accumulated weights to obtain blended probabilities
        safe_weights = np.where(weights > 0, weights, 1.0)
        output = output / safe_weights

        logger.info(
            f"[Tiling] Stitched mask [{output.min():.3f}, {output.max():.3f}]"
        )
        return output

    def _normalize_tile(self, tile: np.ndarray) -> np.ndarray:
        """
        Apply per-band p2-p98 percentile normalization to a (6, H, W) tile.

        This exactly replicates how the training patches were generated:
        each band is independently stretched so that its 2nd percentile maps
        to 0 and its 98th percentile maps to 1.  Values outside that range
        are clipped to [0, 1].

        If a band has fewer than 10 finite pixels (e.g. fully masked by clouds)
        it is filled with 0.5 so the model receives a neutral mid-range value
        rather than zeros or NaNs.

        Args:
            tile: float32 array of shape (6, H, W) with raw HLS reflectance

        Returns:
            np.ndarray: normalized float32 array of shape (6, H, W) in [0, 1]
        """
        normalized = np.zeros_like(tile, dtype=np.float32)
        for b in range(tile.shape[0]):
            band  = tile[b]
            valid = band[np.isfinite(band)]
            if len(valid) > 10:
                p2, p98 = np.percentile(valid, [2, 98])
                if p98 > p2:
                    normalized[b] = np.clip((band - p2) / (p98 - p2), 0.0, 1.0)
                else:
                    normalized[b] = np.full_like(band, 0.5, dtype=np.float32)
            else:
                normalized[b] = np.full_like(band, 0.5, dtype=np.float32)
        return normalized

    def _make_weight_window(self, size: int) -> np.ndarray:
        """
        Build a 2D linear-ramp weight window of shape (size, size).

        The ramp rises from 1 at the edges to size//2 at the centre, then
        falls back symmetrically.  The 2D window is the outer product of two
        such ramps, so corner pixels get the lowest weight and the centre gets
        the highest.  This gives smooth cross-fading in overlap zones with no
        hard seams.
        """
        ramp   = np.minimum(
            np.arange(1, size + 1),
            np.arange(size, 0, -1),
        ).astype(np.float32)
        window = np.outer(ramp, ramp)
        window /= window.max()
        return window

    def get_model_info(self) -> dict:
        """
        Get model information for logging and debugging

        Returns:
            dict: Model metadata
        """
        return {
            'model_type': 'FPN with ResNet50 encoder',
            'architecture': 'Feature Pyramid Network',
            'encoder': 'ResNet50',
            'input_channels': 6,
            'output_classes': 1,
            'device': str(self.device),
            'parameters': sum(p.numel() for p in self.model.parameters()) if hasattr(self.model,
                                                                                     'parameters') else 'mock_model',
            'cuda_available': torch.cuda.is_available(),
            'model_path': os.getenv('MODEL_PATH', 'models/best_precision_model_6band.pth')
        }


# Global singleton instance
_model_singleton = None


def get_inference_service() -> InferenceService:
    """
    Get global inference service instance

    Returns:
        InferenceService: Global service instance
    """
    global _model_singleton
    if _model_singleton is None:
        _model_singleton = InferenceService()
    return _model_singleton