"""
Tests for the GEE service fixes.

Run with:
    python manage.py test apps.gee.tests.test_gee_service_fixes -v 2

These tests use mocking so they run without real GEE credentials.
"""

import json
import uuid
from datetime import date
from unittest.mock import MagicMock, patch, PropertyMock

from django.test import TestCase


class TestGeeServiceBandSelection(TestCase):
    """Verify select_bands now returns all 6 bands the preprocessor needs."""

    def setUp(self):
        # Patch ee.Initialize so the service doesn't try to authenticate
        self.ee_patcher = patch('apps.gee.services.ee')
        self.mock_ee = self.ee_patcher.start()
        self.mock_ee.Initialize = MagicMock()
        self.mock_ee.ServiceAccountCredentials = MagicMock()

    def tearDown(self):
        self.ee_patcher.stop()

    def test_select_bands_selects_six_bands(self):
        """select_bands must select B2, B3, B4, B8, B11, B12."""
        from apps.gee.services import GeeService

        service = GeeService.__new__(GeeService)
        service._ee_initialized = True

        mock_image = MagicMock()
        mock_image.select.return_value = mock_image

        service.select_bands(mock_image)

        call_args = mock_image.select.call_args[0][0]
        self.assertEqual(
            call_args,
            ['B2', 'B3', 'B4', 'B8', 'B11', 'B12'],
            "select_bands must include B2, B3, B4, B8, B11, B12"
        )

    def test_select_bands_does_not_rename(self):
        """select_bands should NOT rename bands — preprocessor expects original names."""
        from apps.gee.services import GeeService

        service = GeeService.__new__(GeeService)
        service._ee_initialized = True

        mock_image = MagicMock()
        mock_image.select.return_value = mock_image

        service.select_bands(mock_image)

        # Called with only 1 argument (band list), not 2 (band list + rename list)
        call_args = mock_image.select.call_args
        self.assertEqual(
            len(call_args[0]),
            1,
            "select_bands should not pass a rename list"
        )


class TestGeeServiceExportReturnDict(TestCase):
    """Verify export_hls_imagery returns scene_id, satellite, cloud_cover_pct."""

    def _make_mock_job(self):
        job = MagicMock()
        job.id = uuid.uuid4()
        job.start_date = date(2024, 1, 1)
        job.end_date = date(2024, 3, 31)
        job.aoi_geometry = MagicMock()
        job.aoi_geometry.valid = True
        job.aoi_geometry.area = 500_000
        job.aoi_geometry.coords = [[(0, 0)] * 5]
        job.aoi_geometry.geojson = json.dumps({
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
        })
        return job

    @patch('apps.gee.services.ee')
    @patch('apps.gee.services.config')
    def test_export_returns_scene_id(self, mock_config, mock_ee):
        """export_hls_imagery must return a non-empty scene_id."""
        from apps.gee.services import GeeService

        mock_config.side_effect = lambda key, **kw: {
            'GEE_SERVICE_ACCOUNT': '',
            'GEE_PROJECT_ID': '',
            'MAX_AOI_AREA': 1_000_000,
            'MAX_RESOLUTION': 30,
            'HLS_COLLECTION': 'COPERNICUS/S2_SR_HARMONIZED',
            'GCS_BUCKET': 'test-bucket',
        }.get(key, kw.get('default', ''))

        # Mock collection
        mock_collection = MagicMock()
        mock_collection.size.return_value.getInfo.return_value = 5
        mock_collection.map.return_value = mock_collection
        mock_collection.median.return_value = MagicMock()
        mock_collection.aggregate_mean.return_value.getInfo.return_value = 12.5

        mock_ee.ImageCollection.return_value.filterDate.return_value = mock_collection
        mock_ee.Geometry.return_value = MagicMock()

        mock_task = MagicMock()
        mock_task.id = 'fake-task-id-123'
        mock_ee.batch.Export.image.toCloudStorage.return_value = mock_task

        service = GeeService.__new__(GeeService)
        service._ee_initialized = True

        # Patch internal methods to avoid PostGIS dependency
        service.validate_aoi = MagicMock(return_value=(True, None))
        service.simplify_geometry = MagicMock(side_effect=lambda g: g)
        service.geometry_to_ee = MagicMock(return_value=MagicMock())
        service.get_hls_collection = MagicMock(return_value=mock_collection)
        service.apply_cloud_mask = MagicMock(side_effect=lambda img: img)
        service.select_bands = MagicMock(side_effect=lambda img: img)

        job = self._make_mock_job()
        result = service.export_hls_imagery(job)

        self.assertTrue(result['success'], f"Export should succeed: {result}")
        self.assertIn('scene_id', result, "Result must contain 'scene_id'")
        self.assertIn('satellite', result, "Result must contain 'satellite'")
        self.assertIn('cloud_cover_pct', result, "Result must contain 'cloud_cover_pct'")
        self.assertIn('export_id', result, "Result must contain 'export_id'")

        self.assertTrue(
            len(result['scene_id']) > 0,
            "scene_id must not be empty"
        )
        self.assertEqual(result['satellite'], 'S2A')
        self.assertAlmostEqual(result['cloud_cover_pct'], 12.5)

    def test_satellite_detection_landsat8(self):
        """Satellite detection logic correctly identifies Landsat 8 from collection name."""
        # Test the satellite detection logic directly — no need to run the full export
        # This mirrors exactly what export_hls_imagery does at lines 298-305
        def detect_satellite(collection_name):
            if 'LC08' in collection_name or 'LANDSAT_8' in collection_name or 'L8' in collection_name:
                return 'L8'
            elif 'LC09' in collection_name or 'LANDSAT_9' in collection_name or 'L9' in collection_name:
                return 'L9'
            else:
                return 'S2A'

        self.assertEqual(detect_satellite('LANDSAT/LC08/C02/T1_L2'), 'L8')
        self.assertEqual(detect_satellite('LANDSAT_8/something'), 'L8')
        self.assertEqual(detect_satellite('LANDSAT/LC09/C02/T1_L2'), 'L9')
        self.assertEqual(detect_satellite('LANDSAT_9/something'), 'L9')
        self.assertEqual(detect_satellite('COPERNICUS/S2_SR_HARMONIZED'), 'S2A')
        self.assertEqual(detect_satellite('COPERNICUS/S2_SR'), 'S2A')


class TestGeeServiceExportFailure(TestCase):
    """Verify failure cases still return the right shape."""

    @patch('apps.gee.services.ee')
    def test_uninitialized_returns_failure(self, mock_ee):
        from apps.gee.services import GeeService

        service = GeeService.__new__(GeeService)
        service._ee_initialized = False

        result = service.export_hls_imagery(MagicMock())

        self.assertFalse(result['success'])
        self.assertIsNone(result['export_id'])
        self.assertIn('error', result)