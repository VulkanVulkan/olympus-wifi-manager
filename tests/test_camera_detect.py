import unittest
from unittest.mock import MagicMock, patch

from olympus_camera_detect import (
    detect_camera,
    is_olympus_camera,
    wifi_gateway,
)


class TestCameraDetection(unittest.TestCase):

    @patch("olympus_camera_detect.subprocess.run")
    def test_wifi_gateway(self, mock_run):
        devices = MagicMock()
        devices.stdout = "wlo1:wifi:connected\n"

        route = MagicMock()
        route.stdout = (
            "default via 192.168.0.10 dev wlo1 "
            "proto dhcp src 192.168.0.3\n"
        )

        mock_run.side_effect = [devices, route]

        self.assertEqual(wifi_gateway(), "192.168.0.10")

    @patch("olympus_camera_detect.subprocess.run")
    def test_no_wifi_connection(self, mock_run):
        result = MagicMock()
        result.stdout = "wlo1:wifi:disconnected\n"
        mock_run.return_value = result

        self.assertIsNone(wifi_gateway())

    @patch("olympus_camera_detect.urllib.request.urlopen")
    def test_valid_camera_response(self, mock_urlopen):
        response = MagicMock()
        response.status = 200
        response.read.return_value = (
            b"<script>wlansd = new Array();</script>"
            b"<a href='/DCIM'>DCIM</a>"
        )
        mock_urlopen.return_value.__enter__.return_value = response

        self.assertTrue(is_olympus_camera("192.168.0.10"))

    @patch("olympus_camera_detect.urllib.request.urlopen")
    def test_invalid_camera_response(self, mock_urlopen):
        response = MagicMock()
        response.status = 200
        response.read.return_value = b"<html>Ordinary router</html>"
        mock_urlopen.return_value.__enter__.return_value = response

        self.assertFalse(is_olympus_camera("192.168.0.1"))

    @patch("olympus_camera_detect.is_olympus_camera")
    @patch("olympus_camera_detect.wifi_gateway")
    def test_detect_camera(self, mock_gateway, mock_camera):
        mock_gateway.return_value = "192.168.0.10"
        mock_camera.return_value = True

        self.assertEqual(detect_camera(), "192.168.0.10")

    @patch("olympus_camera_detect.is_olympus_camera")
    @patch("olympus_camera_detect.wifi_gateway")
    def test_no_camera_found(self, mock_gateway, mock_camera):
        mock_gateway.return_value = None

        self.assertIsNone(detect_camera())
        mock_camera.assert_not_called()


if __name__ == "__main__":
    unittest.main()
