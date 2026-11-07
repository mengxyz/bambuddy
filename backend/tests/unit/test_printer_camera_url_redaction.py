"""Printer readers see the camera URLs without the login secret, whatever shape
the login takes; editors see them as stored."""

from types import SimpleNamespace

import pytest

from backend.app.api.routes.printers import _serialize_printer
from backend.app.schemas.printer import PrinterResponse, PrinterResponseWithSecret

BASE = {
    "id": 1,
    "name": "P",
    "serial_number": "00M09A000000001",
    "ip_address": "192.168.1.10",
    "model": "X1C",
    "is_active": True,
    "created_at": "2026-10-13T00:00:00",
    "updated_at": "2026-10-13T00:00:00",
}


@pytest.mark.parametrize(
    ("stored", "shown"),
    [
        ("rtsp://admin:pw@10.0.0.5:554/s", "rtsp://admin:[REDACTED]@10.0.0.5:554/s"),
        ("http://:pw@10.0.0.5/snap.jpg", "http://:[REDACTED]@10.0.0.5/snap.jpg"),
        ("rtsp://me@example.com:pw@10.0.0.5/s", "rtsp://me@example.com:[REDACTED]@10.0.0.5/s"),
        ("http://secrettoken@10.0.0.5/snap.jpg", "http://[REDACTED]@10.0.0.5/snap.jpg"),
        ("rtsp://admin:p@ss@10.0.0.5/s", "rtsp://admin:[REDACTED]@10.0.0.5/s"),
        ("rtsp://admin:pa:ss@10.0.0.5/s", "rtsp://admin:[REDACTED]@10.0.0.5/s"),
        ("http://admin:pw@10.0.0.5", "http://admin:[REDACTED]@10.0.0.5"),
        ("http://admin:pw@10.0.0.5/s?x=1#y", "http://admin:[REDACTED]@10.0.0.5/s?x=1#y"),
        ("http://10.0.0.5/snap.jpg?a=b@c", "http://10.0.0.5/snap.jpg?a=b@c"),
        ("http://10.0.0.5/path@thing", "http://10.0.0.5/path@thing"),
        # Without a path, an @ in the query or fragment cannot be told from
        # a password containing ? or #: masked rather than risk the password.
        ("http://10.0.0.5?a=b@c", "http://[REDACTED]@c"),
        ("http://10.0.0.5#a@b", "http://[REDACTED]@b"),
        ("rtsp://admin:pa#ss@10.0.0.5:554/s", "rtsp://admin:[REDACTED]@10.0.0.5:554/s"),
        ("rtsp://admin:pa?ss@10.0.0.5/s", "rtsp://admin:[REDACTED]@10.0.0.5/s"),
        ("rtsp://admin:my pw@10.0.0.5/s", "rtsp://admin:[REDACTED]@10.0.0.5/s"),
        ("rtsp://admin:my\tpw@10.0.0.5/s", "rtsp://admin:[REDACTED]@10.0.0.5/s"),
        ("/dev/video0", "/dev/video0"),
        ("", ""),
        (None, None),
    ],
)
def test_readers_never_see_the_login_secret(stored, shown):
    reader = PrinterResponse(**BASE, external_camera_url=stored, external_camera_snapshot_url=stored)
    editor = PrinterResponseWithSecret(
        **BASE, access_code="12345678", external_camera_url=stored, external_camera_snapshot_url=stored
    )

    assert reader.external_camera_url == shown
    assert reader.external_camera_snapshot_url == shown
    assert editor.external_camera_url == stored


def test_route_serializer_keeps_redaction_and_derives_fork_capabilities():
    printer = SimpleNamespace(
        **{**BASE, "model": "H2D"},
        access_code="12345678",
        external_camera_url="rtsp://admin:pw@10.0.0.5/stream",
        plate_detection_roi_x=0.1,
        plate_detection_roi_y=0.2,
        plate_detection_roi_w=0.8,
        plate_detection_roi_h=0.7,
    )

    response = _serialize_printer(printer, include_secret=False)

    assert response.external_camera_url == "rtsp://admin:[REDACTED]@10.0.0.5/stream"
    assert response.supports_chamber_heater is True
    assert response.plate_detection_roi is not None
    assert response.plate_detection_roi.model_dump() == {"x": 0.1, "y": 0.2, "w": 0.8, "h": 0.7}
