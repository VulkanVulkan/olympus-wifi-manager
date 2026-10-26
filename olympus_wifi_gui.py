#!/usr/bin/env python3
"""Olympus Wi-Fi Manager v0.5.0 — Qt photo browser and downloader.

Place alongside olympus_wifi_manager.py (v0.1).
"""
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from collections import defaultdict

from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import QPixmap, QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QFileDialog, QComboBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QProgressBar, QCheckBox,
    QAbstractItemView
)
from olympus_wifi_manager import discover
from olympus_camera_detect import detect_camera


def photo_url(host, path):
    return "http://" + host + urllib.parse.quote(path, safe="/")


def photo_target(root, photo):
    return root / photo.folder.lstrip("/") / photo.name


class DetectionWorker(QThread):
    """Detect a compatible camera without blocking the GUI."""

    detected = Signal(str)
    not_found = Signal()

    def run(self):
        address = detect_camera()
        if address:
            self.detected.emit(address)
        else:
            self.not_found.emit()


class ScanWorker(QThread):
    result = Signal(object)
    error = Signal(str)

    def __init__(self, host):
        super().__init__()
        self.host = host

    def run(self):
        try:
            self.result.emit(discover("http://" + self.host))
        except Exception as e:
            self.error.emit(str(e))


class ThumbnailWorker(QThread):
    thumbnail = Signal(str, bytes)
    error = Signal(str)

    def __init__(self, host, photos):
        super().__init__()
        self.host = host
        self.photos = photos
        self.stop_requested = False

    def run(self):
        # One representative JPEG per basename; never download full-resolution
        # images for thumbnails.
        for photo in self.photos:
            if self.stop_requested:
                break
            query = urllib.parse.urlencode({"DIR": photo.path})
            url = f"http://{self.host}/get_thumbnail.cgi?{query}"
            try:
                with urllib.request.urlopen(url, timeout=8) as response:
                    data = response.read(2 * 1024 * 1024 + 1)
                if len(data) <= 2 * 1024 * 1024:
                    self.thumbnail.emit(photo.folder + "/" + Path(photo.name).stem, data)
            except Exception:
                # Some firmware versions may not expose thumbnails for all files.
                pass


class DownloadWorker(QThread):
    progress = Signal(int, int, int, int, str)
    done = Signal(str)
    error = Signal(str)

    def __init__(self, host, photos, destination):
        super().__init__()
        self.host = host
        self.photos = photos
        self.destination = destination
        self.stop_requested = False

    def run(self):
        count = len(self.photos)
        downloaded = skipped = 0
        for i, photo in enumerate(self.photos):
            if self.stop_requested:
                self.done.emit(f"Cancelled: {downloaded} downloaded, {skipped} skipped.")
                return
            target = photo_target(self.destination, photo)
            part = target.with_name(target.name + ".part")
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.is_file() and target.stat().st_size == photo.size:
                    skipped += 1
                    self.progress.emit(i, count, photo.size, photo.size, f"Skipped {photo.name}")
                    continue
                url = photo_url(self.host, photo.path)
                received = 0
                with urllib.request.urlopen(url, timeout=30) as response, part.open("wb") as output:
                    while True:
                        if self.stop_requested:
                            self.done.emit(f"Cancelled: {downloaded} downloaded, {skipped} skipped.")
                            return
                        chunk = response.read(128 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        received += len(chunk)
                        self.progress.emit(i, count, received, photo.size, photo.name)
                if received != photo.size:
                    raise ValueError(f"{photo.name}: expected {photo.size} bytes, received {received}")
                part.replace(target)
                downloaded += 1
                self.progress.emit(i, count, received, photo.size, f"Downloaded {photo.name}")
            except Exception as e:
                self.error.emit(f"{photo.name}: {e}")
                return
            finally:
                part.unlink(missing_ok=True)
        self.done.emit(f"Complete: {downloaded} downloaded, {skipped} skipped.")


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Olympus Wi-Fi Manager v0.5.0")
        self.resize(980, 650)
        self.photos = []
        self.groups = []
        self.scan = self.thumbs = self.transfer = None
        self.detection = None
        self.thumbnail_cache = {}
        self.checked_keys = set()

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        top = QHBoxLayout()
        top.addWidget(QLabel("Camera:"))
        self.host = QLineEdit("192.168.0.10")
        top.addWidget(self.host)
        self.detect_button = QPushButton("Detect Camera")
        self.detect_button.clicked.connect(self.detect_camera)
        top.addWidget(self.detect_button)
        self.connect_button = QPushButton("Connect / Refresh")
        self.connect_button.clicked.connect(self.refresh)
        top.addWidget(self.connect_button)
        layout.addLayout(top)

        options = QHBoxLayout()
        options.addWidget(QLabel("Show:"))
        self.filter = QComboBox()
        self.filter.addItems(["JPEG + RAW", "JPEG only", "RAW only", "Both formats present"])
        self.filter.currentIndexChanged.connect(self.populate)
        options.addWidget(self.filter)
        self.select_button = QPushButton("Select visible")
        self.select_button.clicked.connect(lambda: self.check_visible(True))
        options.addWidget(self.select_button)
        self.clear_button = QPushButton("Clear visible")
        self.clear_button.clicked.connect(lambda: self.check_visible(False))
        options.addWidget(self.clear_button)
        options.addStretch()
        layout.addLayout(options)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Select", "Preview", "Photograph", "Formats", "Total MiB"])
        self.table.setIconSize(QSize(180, 135))
        self.table.verticalHeader().setDefaultSectionSize(150)
        self.table.setColumnWidth(0, 65)
        self.table.setColumnWidth(1, 200)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(False)
        self.table.itemChanged.connect(self.selection_changed)
        layout.addWidget(self.table)

        destination = QHBoxLayout()
        destination.addWidget(QLabel("Save to:"))
        self.dest = QLineEdit(str(Path.home() / "Pictures" / "Olympus-WiFi"))
        destination.addWidget(self.dest)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse)
        destination.addWidget(browse)
        layout.addLayout(destination)

        controls = QHBoxLayout()
        self.download_button = QPushButton("Download selected")
        self.download_button.clicked.connect(self.download)
        controls.addWidget(self.download_button)
        self.cancel_button = QPushButton("Cancel transfer")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        controls.addWidget(self.cancel_button)
        layout.addLayout(controls)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.status = QLabel("Connect to the Olympus Wi-Fi network, then click Connect / Refresh.")
        layout.addWidget(self.status)

    def key(self, photo):
        return photo.folder + "/" + Path(photo.name).stem



    def detect_camera(self):
        if (
            self.detection is not None
            or self.scan is not None
            or self.thumbs is not None
            or self.transfer is not None
        ):
            return

        self.detect_button.setEnabled(False)
        self.status.setText("Detecting Olympus camera…")

        self.detection = DetectionWorker()
        self.detection.detected.connect(self.camera_detected)
        self.detection.not_found.connect(self.camera_not_found)
        self.detection.finished.connect(self.detection_finished)
        self.detection.start()

    def camera_detected(self, address):
        self.host.setText(address)
        self.status.setText(
            f"Camera detected at {address}. Click Connect / Refresh."
        )

    def camera_not_found(self):
        self.status.setText(
            "No compatible camera detected. Check Wi-Fi or enter the IP manually."
        )

    def detection_finished(self):
        self.detection = None
        self.detect_button.setEnabled(True)



    def refresh(self):
        if (
            self.detection is not None
            or self.scan is not None
            or self.transfer is not None
            or self.thumbs is not None
        ):
            return

        host = self.host.text().strip()
        if not host or any(c in host for c in "/:? #"):
            self.status.setText("Enter a camera IP address, e.g. 192.168.0.10")
            return
        self.checked_keys.clear()
        self.thumbnail_cache.clear()
        self.status.setText("Scanning camera directories…")
        self.connect_button.setEnabled(False)
        self.scan = ScanWorker(host)
        self.scan.result.connect(self.scanned)
        self.scan.error.connect(self.fail)
        self.scan.finished.connect(self.scan_finished)
        self.scan.start()

    def scan_finished(self):
        self.scan = None
        self.connect_button.setEnabled(True)

    def scanned(self, photos):
        self.photos = photos
        grouped = defaultdict(list)
        for photo in photos:
            grouped[self.key(photo)].append(photo)
        self.groups = sorted(grouped.items())
        self.populate()
        self.status.setText(f"Connected: {len(photos)} files in {len(self.groups)} photo groups.")
        # Request a single JPEG thumbnail per group. RAW-only groups may lack previews.
        reps = [next((p for p in members if p.name.upper().endswith((".JPG", ".JPEG"))), members[0])
                for _, members in self.groups]
        self.thumbs = ThumbnailWorker(self.host.text().strip(), reps)
        self.thumbs.thumbnail.connect(self.show_thumbnail)
        self.thumbs.finished.connect(self.thumbnails_finished)
        self.thumbs.start()

    def thumbnails_finished(self):
        self.thumbs = None

    def visible_groups(self):
        mode = self.filter.currentIndex()
        for key, photos in self.groups:
            extensions = {Path(p.name).suffix.upper() for p in photos}
            jpg = bool(extensions & {".JPG", ".JPEG"})
            raw = ".ORF" in extensions
            if mode == 1 and not jpg:
                continue
            if mode == 2 and not raw:
                continue
            if mode == 3 and not (jpg and raw):
                continue
            yield key, photos

    def displayed_files(self, photos):
        mode = self.filter.currentIndex()
        if mode == 1:
            return [p for p in photos if Path(p.name).suffix.upper() in {".JPG", ".JPEG"}]
        if mode == 2:
            return [p for p in photos if Path(p.name).suffix.upper() == ".ORF"]
        return photos

    def selection_changed(self, item):
        if item.column() != 0:
            return
        name_item = self.table.item(item.row(), 2)
        if name_item is None:
            return
        paths = name_item.data(Qt.ItemDataRole.UserRole + 1) or []
        if item.checkState() == Qt.CheckState.Checked:
            self.checked_keys.update(paths)
        elif item.checkState() == Qt.CheckState.Unchecked:
            self.checked_keys.difference_update(paths)
        # A partially checked pair is informational: do not alter its members.

    def populate(self):
        mode = self.filter.currentIndex()
        visible = list(self.visible_groups())
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(len(visible))
            for row, (key, photos) in enumerate(visible):
                displayed = self.displayed_files(photos)
                paths = [p.path for p in displayed]
                selected_count = sum(path in self.checked_keys for path in paths)
                if selected_count == len(paths) and paths:
                    state = Qt.CheckState.Checked
                elif selected_count:
                    state = Qt.CheckState.PartiallyChecked
                else:
                    state = Qt.CheckState.Unchecked
                tick = QTableWidgetItem()
                tick.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                tick.setCheckState(state)
                self.table.setItem(row, 0, tick)
                self.table.setItem(row, 1, QTableWidgetItem())
                name = QTableWidgetItem(Path(key).name)
                name.setData(Qt.ItemDataRole.UserRole, key)
                name.setData(Qt.ItemDataRole.UserRole + 1, paths)
                self.table.setItem(row, 2, name)
                formats = " + ".join(sorted({Path(p.name).suffix.upper().lstrip(".") for p in displayed}))
                self.table.setItem(row, 3, QTableWidgetItem(formats))
                size = QTableWidgetItem(f"{sum(p.size for p in displayed) / 1048576:.2f}")
                size.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, 4, size)
                self.set_icon(row, key)
        finally:
            self.table.blockSignals(False)

    def set_icon(self, row, key):
        data = self.thumbnail_cache.get(key)
        if data:
            pix = QPixmap()
            if pix.loadFromData(data):
                self.table.item(row, 1).setIcon(QIcon(pix.scaled(180, 135, Qt.AspectRatioMode.KeepAspectRatio,
                                                             Qt.TransformationMode.SmoothTransformation)))

    def show_thumbnail(self, key, data):
        self.thumbnail_cache[key] = data
        for row in range(self.table.rowCount()):
            if self.table.item(row, 2).data(Qt.ItemDataRole.UserRole) == key:
                self.set_icon(row, key)

    def check_visible(self, checked):
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )

    def browse(self):
        selected = QFileDialog.getExistingDirectory(self, "Download destination", self.dest.text())
        if selected:
            self.dest.setText(selected)

    def download(self):
        if self.transfer is not None or self.scan is not None:
            return
        # Download selected files in the CURRENT visible format, not hidden selections.
        visible_paths = set()
        for _, photos in self.visible_groups():
            visible_paths.update(p.path for p in self.displayed_files(photos))
        selected = [p for p in self.photos if p.path in self.checked_keys and p.path in visible_paths]
        if not selected:
            self.status.setText("No files match the selection and format filter.")
            return
        if not self.dest.text().strip():
            self.status.setText("Choose a destination folder.")
            return
        self.transfer = DownloadWorker(self.host.text().strip(), selected, Path(self.dest.text()).expanduser())
        self.transfer.progress.connect(self.update_progress)
        self.transfer.done.connect(self.completed)
        self.transfer.error.connect(self.fail)
        self.transfer.finished.connect(self.transfer_finished)
        self.download_button.setEnabled(False)
        self.connect_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setValue(0)
        self.status.setText(f"Downloading {len(selected)} file(s)…")
        self.transfer.start()

    def update_progress(self, finished, total, received, expected, filename):
        fractional = received / expected if expected else 1
        self.progress.setValue(min(100, round(100 * (finished + fractional) / total)))
        self.status.setText(f"{filename} — {received / 1048576:.1f}/{expected / 1048576:.1f} MiB; file {min(finished + 1, total)}/{total}")

    def completed(self, message):
        self.status.setText(message)
        if message.startswith("Complete"):
            self.progress.setValue(100)

    def transfer_finished(self):
        self.transfer = None
        self.download_button.setEnabled(True)
        self.connect_button.setEnabled(True)
        self.cancel_button.setEnabled(False)

    def cancel(self):
        if self.transfer:
            self.transfer.stop_requested = True
            self.cancel_button.setEnabled(False)
            self.status.setText("Cancelling…")

    def fail(self, message):
        self.status.setText(message)
        QMessageBox.warning(self, "Olympus Wi-Fi Manager", message)

    def closeEvent(self, event):
        if any(w is not None and w.isRunning() for w in (self.scan, self.thumbs, self.transfer)):
            QMessageBox.information(self, "Please wait", "Wait for the current camera operation to finish before closing.")
            event.ignore()
        else:
            event.accept()


def main():
    app = QApplication(sys.argv)
    window = Window()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
