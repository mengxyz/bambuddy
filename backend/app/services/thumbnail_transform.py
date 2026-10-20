"""Small-display thumbnail resizing shared by cover and file-browser routes."""

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, UnidentifiedImageError


class ThumbnailTransformError(ValueError):
    """The stored image cannot be decoded or has invalid dimensions."""


def transform_thumbnail(
    image_data: bytes,
    width: int | None,
    height: int | None,
    image_format: str,
    quality: int,
) -> bytes:
    """Resize without stretching and encode as PNG or JPEG.

    Two dimensions produce an exact-size, dark-letterboxed image. One
    dimension preserves the source aspect ratio. The dark background also
    makes transparent slicer previews deterministic on embedded displays.
    """
    try:
        with Image.open(io.BytesIO(image_data)) as source:
            source.load()
            rgba = source.convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ThumbnailTransformError("Thumbnail image could not be decoded") from exc

    source_width, source_height = rgba.size
    if source_width <= 0 or source_height <= 0:
        raise ThumbnailTransformError("Thumbnail image has invalid dimensions")

    if width is None and height is None:
        target_width, target_height = source_width, source_height
    elif width is None:
        target_height = height
        target_width = max(1, round(source_width * target_height / source_height))
    elif height is None:
        target_width = width
        target_height = max(1, round(source_height * target_width / source_width))
    else:
        target_width, target_height = width, height

    resized = rgba.copy()
    resized.thumbnail((target_width, target_height), Image.Resampling.LANCZOS)

    canvas = Image.new("RGB", (target_width, target_height), (24, 24, 24))
    left = (target_width - resized.width) // 2
    top = (target_height - resized.height) // 2
    canvas.paste(resized.convert("RGB"), (left, top), resized.getchannel("A"))

    output = io.BytesIO()
    if image_format == "jpeg":
        canvas.save(output, "JPEG", quality=quality, optimize=True)
    else:
        canvas.save(output, "PNG", optimize=True)
    return output.getvalue()


@lru_cache(maxsize=256)
def transform_thumbnail_file(
    path: str,
    mtime_ns: int,
    file_size: int,
    width: int | None,
    height: int | None,
    image_format: str,
    quality: int,
) -> bytes:
    """Read and transform a stored thumbnail with a bounded in-memory cache.

    ``mtime_ns`` and ``file_size`` are part of the key so replacing a thumbnail
    at the same path cannot serve stale pixels. They are otherwise unused.
    """
    del mtime_ns, file_size
    return transform_thumbnail(Path(path).read_bytes(), width, height, image_format, quality)
