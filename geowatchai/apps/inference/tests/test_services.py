"""
Unit tests for Inference Service.

Tests cover:
- Model loading with exact SMP FPN architecture
- 6-channel tensor processing from preprocessing
- Device management (CUDA/CPU)
- Prediction pipeline with sigmoid activation
- Error handling and edge cases
"""

import pytest
import numpy as np
import torch
import torch.nn as nn
from unittest.mock import patch, MagicMock, mock_open
import tempfile
import os
from pathlib import Path

# Set up Django before importing test classes
import django
from django.conf import settings
if not settings.configured:
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    django.setup()

from django.test import TestCase
from apps.inference.services import InferenceService, ModelSingleton, get_inference_service


@pytest.mark.django_db
class ModelSingletonTest(TestCase):
    """Test model singleton pattern following Anti-Vibe guardrails"""
    
    def setUp(self):
        """Set up test fixtures before each test method."""
        # Clear any existing singleton instance
        import apps.inference.services
        apps.inference.services.ModelSingleton._instance = None
        apps.inference.services.ModelSingleton._model = None
        apps.inference.services.ModelSingleton._device = None
    
    def test_singleton_pattern(self):
        """Test that only one instance is created"""
        instance1 = ModelSingleton()
        instance2 = ModelSingleton()
        
        self.assertIs(instance1, instance2)
    
    @patch('apps.inference.services.smp.FPN')
    @patch('apps.inference.services.torch.load')
    @patch('apps.inference.services.os.path.exists')
    def test_load_real_model_success(self, mock_exists, mock_torch_load, mock_fpn):
        """Test successful loading of real FPN model."""
        # Mock model file exists
        mock_exists.return_value = True
        
        # Mock checkpoint data
        mock_checkpoint = {
            'model_state_dict': {'conv.weight': torch.zeros(1, 6, 1, 1)},
            'epoch': 10
        }
        mock_torch_load.return_value = mock_checkpoint
        
        # Mock FPN model
        mock_model = MagicMock()
        mock_model.parameters.return_value = [torch.zeros(100)]
        mock_fpn.return_value = mock_model
        
        # Create singleton (should trigger model loading)
        singleton = ModelSingleton()
        
        # Verify FPN was created with correct parameters
        mock_fpn.assert_called_once_with(
            encoder_name='resnet50',
            encoder_weights=None,
            in_channels=6,
            classes=1,
            activation=None
        )
        
        # Verify model was loaded and set to eval
        mock_model.load_state_dict.assert_called_once_with(mock_checkpoint['model_state_dict'])
        mock_model.eval.assert_called_once()
        
        self.assertIsNotNone(singleton.model)
    
    @patch('apps.inference.services.os.path.exists')
    def test_load_model_file_not_found(self, mock_exists):
        """Test fallback to mock model when file doesn't exist."""
        mock_exists.return_value = False
        
        singleton = ModelSingleton()
        
        # Should create mock model
        self.assertIsNotNone(singleton.model)
        self.assertEqual(str(singleton.device), 'cpu')
    
    @patch('apps.inference.services.smp.FPN')
    @patch('apps.inference.services.torch.load')
    @patch('apps.inference.services.os.path.exists')
    @patch('apps.inference.services.torch.cuda.is_available')
    def test_cuda_device_selection(self, mock_cuda, mock_exists, mock_torch_load, mock_fpn):
        """Test CUDA device selection when available."""
        mock_cuda.return_value = True
        mock_exists.return_value = True
        
        mock_checkpoint = {'model_state_dict': {}}
        mock_torch_load.return_value = mock_checkpoint
        
        mock_model = MagicMock()
        mock_model.parameters.return_value = []
        mock_fpn.return_value = mock_model
        
        singleton = ModelSingleton()
        
        # Should select CUDA device
        self.assertEqual(str(singleton.device), 'cuda')
        mock_model.to.assert_called_with(torch.device('cuda'))
    
    def test_mock_model_architecture(self):
        """Test mock model has correct architecture."""
        singleton = ModelSingleton()
        mock_model = singleton._create_mock_model()
        
        # Should have conv layer that maps 6 channels to 1
        self.assertTrue(hasattr(mock_model, 'conv'))
        
        # Check first layer input channels
        first_layer = mock_model.conv[0]
        self.assertEqual(first_layer.in_channels, 6)
        
        # Check final layer output channels
        final_layer = mock_model.conv[-1]
        self.assertEqual(final_layer.out_channels, 1)
        
        # Test forward pass
        test_input = torch.randn(1, 6, 32, 32)
        output = mock_model(test_input)
        
        self.assertEqual(output.shape[0], 1)  # Batch size
        self.assertEqual(output.shape[1], 1)  # Output channels
        self.assertEqual(output.shape[2], 32)  # Height
        self.assertEqual(output.shape[3], 32)  # Width


@pytest.mark.django_db
class InferenceServiceTest(TestCase):
    """Test inference service business logic"""
    
    def setUp(self):
        """Set up test data"""
        # Create a mock model that mimics FPN behavior
        self.mock_model = MagicMock()
        
        # Create service with mocked model
        self.inference_service = InferenceService()
        self.inference_service.model = self.mock_model
        self.inference_service.device = torch.device('cpu')
        
        # Create test 6-channel tensors (from preprocessing)
        self.mock_tensors = [
            np.random.rand(6, 64, 64).astype(np.float32),
            np.random.rand(6, 64, 64).astype(np.float32),
        ]
    
    def test_predict_success(self):
        """Test successful prediction with 6-channel input."""
        input_tensor = self.mock_tensors[0]
        
        # Mock model output (raw logits)
        mock_output = torch.randn(1, 1, 64, 64)
        self.mock_model.return_value = mock_output
        
        # Run prediction
        result = self.inference_service.predict(input_tensor)
        
        # Verify model was called with correct input
        self.mock_model.assert_called_once()
        call_args = self.mock_model.call_args[0][0]
        
        # Should have batch dimension and be on correct device
        self.assertEqual(call_args.shape, (1, 6, 64, 64))
        self.assertEqual(call_args.device.type, 'cpu')
        
        # Verify output shape and type
        self.assertEqual(result.shape, (1, 64, 64))
        self.assertIsInstance(result, np.ndarray)
        
        # Verify sigmoid was applied (values should be in [0, 1])
        self.assertTrue(0 <= result.min() <= result.max() <= 1)
    
    def test_predict_with_torch_tensor_input(self):
        """Test prediction with PyTorch tensor input."""
        input_tensor = torch.rand(6, 64, 64)
        
        mock_output = torch.randn(1, 1, 64, 64)
        self.mock_model.return_value = mock_output
        
        result = self.inference_service.predict(input_tensor)
        
        self.assertEqual(result.shape, (1, 64, 64))
        self.assertIsInstance(result, np.ndarray)
    
    def test_predict_invalid_shape(self):
        """Test prediction fails with invalid input shape."""
        # Wrong number of channels
        invalid_tensor = np.random.rand(3, 64, 64).astype(np.float32)
        
        with self.assertRaises(ValueError) as cm:
            self.inference_service.predict(invalid_tensor)
        self.assertIn("Expected 6-channel tensor", str(cm.exception))
    
    def test_predict_none_input(self):
        """Test prediction fails with None input."""
        with self.assertRaises(ValueError) as cm:
            self.inference_service.predict(None)
        self.assertIn("No preprocessed tensor provided", str(cm.exception))
    
    def test_predict_batch_success(self):
        """Test successful batch prediction."""
        # Mock model output for batch
        mock_output = torch.randn(2, 1, 64, 64)
        self.mock_model.return_value = mock_output
        
        results = self.inference_service.predict_batch(self.mock_tensors)
        
        # Verify correct number of results
        self.assertEqual(len(results), 2)
        
        # Verify each result shape
        for result in results:
            self.assertEqual(result.shape, (1, 64, 64))
            self.assertIsInstance(result, np.ndarray)
            self.assertTrue(0 <= result.min() <= result.max() <= 1)
    
    def test_predict_batch_empty(self):
        """Test batch prediction with empty input."""
        with self.assertRaises(ValueError) as cm:
            self.inference_service.predict_batch([])
        self.assertIn("No preprocessed tensors provided", str(cm.exception))
    
    def test_predict_single_success(self):
        """Test successful single prediction."""
        mock_output = torch.randn(1, 1, 64, 64)
        self.mock_model.return_value = mock_output
        
        result = self.inference_service.predict_single(self.mock_tensors[0])
        
        self.assertEqual(result.shape, (1, 64, 64))
        self.assertIsInstance(result, np.ndarray)
    
    def test_get_model_info(self):
        """Test model info retrieval."""
        # Mock model parameters
        mock_param = torch.zeros(100)
        self.mock_model.parameters.return_value = [mock_param]
        
        info = self.inference_service.get_model_info()
        
        self.assertEqual(info['model_type'], 'FPN with ResNet50 encoder')
        self.assertEqual(info['architecture'], 'Feature Pyramid Network')
        self.assertEqual(info['encoder'], 'ResNet50')
        self.assertEqual(info['input_channels'], 6)
        self.assertEqual(info['output_classes'], 1)
        self.assertEqual(info['device'], 'cpu')
        self.assertEqual(info['parameters'], 100)
        self.assertIn('cuda_available', info)
        self.assertIn('model_path', info)


@pytest.mark.django_db
class GlobalInferenceServiceTest(TestCase):
    """Test global inference service function"""
    
    def setUp(self):
        """Clear singleton before tests."""
        import apps.inference.services
        apps.inference.services._model_singleton = None
    
    def test_get_inference_service_singleton(self):
        """Test that global function returns singleton"""
        service1 = get_inference_service()
        service2 = get_inference_service()
        
        self.assertIs(service1, service2)
        self.assertIsInstance(service1, InferenceService)
    
    @patch('apps.inference.services.os.path.exists')
    def test_full_pipeline_with_mock_model(self, mock_exists):
        """Test full inference pipeline with mock model."""
        mock_exists.return_value = False  # Force mock model usage
        
        service = get_inference_service()
        
        # Create realistic 6-channel input
        input_tensor = np.random.rand(6, 128, 128).astype(np.float32)
        
        # Run inference
        result = service.predict(input_tensor)
        
        # Verify output
        self.assertEqual(result.shape, (1, 128, 128))
        self.assertIsInstance(result, np.ndarray)
        self.assertTrue(0 <= result.min() <= result.max() <= 1)
        
        # Verify model info
        info = service.get_model_info()
        self.assertEqual(info['input_channels'], 6)
        self.assertEqual(info['output_classes'], 1)
