import io

import pytest
from PIL import Image

from backend.app.services.thumbnail_transform import ThumbnailTransformError, transform_thumbnail


def _png(size: tuple[int, int], color: tuple[int, int, int]) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, "PNG")
    return output.getvalue()


def test_exact_dimensions_letterbox_without_stretching():
    result = transform_thumbnail(_png((400, 200), (220, 30, 30)), 240, 240, "jpeg", 80)

    with Image.open(io.BytesIO(result)) as image:
        assert image.format == "JPEG"
        assert image.size == (240, 240)
        assert max(image.getpixel((120, 10))) < 40
        assert image.getpixel((120, 120))[0] > 180


def test_one_dimension_preserves_aspect_ratio():
    result = transform_thumbnail(_png((400, 200), (0, 174, 66)), 120, None, "png", 80)

    with Image.open(io.BytesIO(result)) as image:
        assert image.format == "PNG"
        assert image.size == (120, 60)


def test_invalid_stored_image_is_rejected():
    with pytest.raises(ThumbnailTransformError):
        transform_thumbnail(b"not an image", 240, 240, "jpeg", 80)
