import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import olympus_wifi_manager as m


class ManagerTests(unittest.TestCase):
    def test_parse_camera_entries(self):
        page = 'wlansd[0]="/DCIM,101OLYMP,0,16,1,2";\nwlansd[1]="/DCIM/101OLYMP,SRGB5177.ORF,18913370,0,1,2";'
        self.assertEqual(list(m.entries(page)), [
            ('/DCIM', '101OLYMP', 0, 16),
            ('/DCIM/101OLYMP', 'SRGB5177.ORF', 18913370, 0),
        ])

    def test_reject_path_traversal(self):
        page = 'wlansd[0]="/DCIM/101OLYMP,../bad.jpg,100,0,1,2";'
        self.assertEqual(list(m.entries(page)), [])

    def test_discover_jpeg_and_raw(self):
        pages = {
            '/DCIM': 'wlansd[0]="/DCIM,101OLYMP,0,16,1,2";',
            '/DCIM/101OLYMP': (
                'wlansd[0]="/DCIM/101OLYMP,A.JPG,10,0,1,2";'
                'wlansd[1]="/DCIM/101OLYMP,A.ORF,20,0,1,2";'
                'wlansd[2]="/DCIM/101OLYMP,OTHER.TXT,5,0,1,2";'
            )
        }
        with patch.object(m, 'get', side_effect=lambda base, path: pages[path]):
            photos = m.discover('http://192.168.0.10')
        self.assertEqual([p.name for p in photos], ['A.JPG', 'A.ORF'])

    def test_existing_same_size_skips(self):
        with tempfile.TemporaryDirectory() as td:
            photo = m.Photo('/DCIM/101OLYMP', 'A.JPG', 4)
            target = Path(td) / 'DCIM/101OLYMP/A.JPG'
            target.parent.mkdir(parents=True)
            target.write_bytes(b'abcd')
            with patch.object(m.urllib.request, 'urlopen', side_effect=AssertionError('should not download')):
                m.download('http://192.168.0.10', photo, Path(td))
            self.assertEqual(target.read_bytes(), b'abcd')


if __name__ == '__main__':
    unittest.main()
