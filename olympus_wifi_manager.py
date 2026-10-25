#!/usr/bin/env python3
"""Read-only Olympus Wi-Fi photo manager (standard library only)."""
import argparse
import html
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ENTRY = re.compile(r'wlansd\[\d+\]\s*=\s*["\']([^"\']+)["\']')
SUPPORTED = {'.JPG', '.JPEG', '.ORF'}

@dataclass(frozen=True)
class Photo:
    folder: str
    name: str
    size: int

    @property
    def path(self):
        return self.folder.rstrip('/') + '/' + self.name


def get(base, path, timeout=10):
    url = base.rstrip('/') + '/' + path.lstrip('/')
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read().decode('shift_jis', errors='replace')


def entries(page):
    for match in ENTRY.finditer(page):
        fields = html.unescape(match.group(1)).split(',')
        if len(fields) < 4:
            continue
        folder, name, size, attrs = fields[:4]
        if not folder.startswith('/DCIM') or not name or '/' in name or '\\' in name or name in {'.', '..'}:
            continue
        try:
            size, attrs = int(size), int(attrs)
        except ValueError:
            continue
        yield folder, name, size, attrs


def discover(base):
    root = list(entries(get(base, '/DCIM')))
    folders = [folder.rstrip('/') + '/' + name for folder, name, _, attrs in root if attrs & 0x10]
    photos = []
    for folder in folders:
        for parent, name, size, attrs in entries(get(base, folder)):
            if not (attrs & 0x10) and Path(name).suffix.upper() in SUPPORTED and parent.rstrip('/') == folder:
                photos.append(Photo(parent, name, size))
    return sorted(photos, key=lambda p: (p.folder, p.name))


def download(base, photo, destination):
    relative = Path(photo.folder.lstrip('/')) / photo.name
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.stat().st_size == photo.size:
        print(f'SKIP {relative} (size matches)')
        return
    part = target.with_name(target.name + '.part')
    url = base.rstrip('/') + urllib.parse.quote(photo.path, safe='/')
    try:
        with urllib.request.urlopen(url, timeout=30) as response, part.open('wb') as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        actual = part.stat().st_size
        if actual != photo.size:
            raise ValueError(f'expected {photo.size} bytes, got {actual}')
        part.replace(target)
        print(f'OK   {relative} ({actual} bytes)')
    except Exception:
        part.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description='Browse/download Olympus camera photos over Wi-Fi')
    parser.add_argument('--host', default='192.168.0.10', help='camera IP address')
    parser.add_argument('--dest', type=Path, default=Path.home() / 'Pictures' / 'Olympus-WiFi')
    parser.add_argument('--list', action='store_true', help='list files (default when no download option)')
    parser.add_argument('--download', metavar='NAME', action='append', help='download exact filename; repeatable')
    parser.add_argument('--all', action='store_true', help='download all JPG and ORF files')
    parser.add_argument('--format', choices=['all', 'jpg', 'orf'], default='all')
    args = parser.parse_args()
    base = 'http://' + args.host
    try:
        photos = discover(base)
    except (urllib.error.URLError, ValueError, OSError) as exc:
        parser.exit(1, f'Camera not reachable or directory unreadable: {exc}\n')
    if not photos:
        parser.exit(1, 'No supported photos found (or camera directory layout differs).\n')
    photos = [p for p in photos if args.format == 'all' or p.name.upper().endswith('.' + args.format.upper())]
    print(f'Camera {args.host}: {len(photos)} matching file(s)')
    if args.list or not (args.download or args.all):
        for p in photos:
            print(f'{p.size:>12}  {p.path}')
    if args.download or args.all:
        wanted = set(args.download or [])
        selected = photos if args.all else [p for p in photos if p.name in wanted]
        if wanted:
            missing = wanted - {p.name for p in selected}
            if missing:
                print('Not found: ' + ', '.join(sorted(missing)), file=sys.stderr)
        failures = 0
        for p in selected:
            try:
                download(base, p, args.dest)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                failures += 1
                print(f'ERROR {p.path}: {exc}', file=sys.stderr)
        if failures or (wanted and missing):
            return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
