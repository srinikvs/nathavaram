"""Public Issues photos: decode, shrink, strip metadata, and store outside the web root.

Every upload is re-encoded as JPEG. Re-encoding drops EXIF, GPS and other metadata, and the
long edge is capped at MAX_EDGE_PX. Images smaller than the cap are never enlarged.
Files are written under <photo dir>/<issue id>/<random>.jpg, so no client-supplied name is used.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

from app.errors import ServiceError

MAX_EDGE_PX = 1600
JPEG_QUALITY = 85
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
ACCEPTED_FORMATS = frozenset({"JPEG", "PNG", "HEIF"})
ACCEPTED_CONTENT_TYPES = ("image/jpeg", "image/png", "image/heic", "image/heif")
FORMAT_REFUSED_MESSAGE = "Upload a JPG, PNG or HEIC photo"
HEIC_UNAVAILABLE_MESSAGE = "HEIC photos are not supported on this server; upload a JPG or PNG"
WHITE = (255, 255, 255)

try:  # pillow-heif is optional at import time so JPEG and PNG work without it.
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIC_SUPPORTED = True
except ImportError:
    HEIC_SUPPORTED = False


@dataclass(frozen=True)
class StoredPhoto:
    storage_name: str
    width: int
    height: int


def _flatten_to_rgb(image: Image.Image) -> Image.Image:
    """JPEG has no alpha channel: transparent pixels become white."""
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, WHITE)
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


def _shrink(image: Image.Image) -> Image.Image:
    width, height = image.size
    longest = max(width, height)
    if longest <= MAX_EDGE_PX:
        return image
    scale = MAX_EDGE_PX / longest
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return image.resize(size, Image.Resampling.LANCZOS)


def _detect_format(data: bytes) -> str:
    try:
        with Image.open(BytesIO(data)) as probe:
            fmt = probe.format
    except Exception as exc:  # Pillow raises several types for unreadable input
        raise ServiceError(FORMAT_REFUSED_MESSAGE, 422, "photo") from exc
    if fmt not in ACCEPTED_FORMATS:
        raise ServiceError(FORMAT_REFUSED_MESSAGE, 422, "photo")
    if fmt == "HEIF" and not HEIC_SUPPORTED:
        raise ServiceError(HEIC_UNAVAILABLE_MESSAGE, 415, "photo")
    return fmt


def normalize_image(data: bytes) -> tuple[bytes, int, int]:
    """Return (JPEG bytes without metadata, width, height) for an accepted upload."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise ServiceError("Photo is larger than 15 MB", 413, "photo")
    _detect_format(data)
    try:
        with Image.open(BytesIO(data)) as source:
            # exif_transpose applies the camera rotation; the result has no EXIF to keep.
            oriented = ImageOps.exif_transpose(source)
        image = _shrink(_flatten_to_rgb(oriented))
        out = BytesIO()
        image.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    except ServiceError:
        raise
    except Exception as exc:
        raise ServiceError(FORMAT_REFUSED_MESSAGE, 422, "photo") from exc
    width, height = image.size
    return out.getvalue(), width, height


def store_photo(data: bytes, photo_dir: Path, issue_id: int) -> StoredPhoto:
    payload, width, height = normalize_image(data)
    folder = photo_dir / str(issue_id)
    folder.mkdir(parents=True, exist_ok=True)
    storage_name = f"{issue_id}/{uuid.uuid4().hex}.jpg"
    (photo_dir / storage_name).write_bytes(payload)
    return StoredPhoto(storage_name=storage_name, width=width, height=height)


def photo_path(photo_dir: Path, storage_name: str) -> Path:
    """Absolute path of a stored photo. Refuses any name that escapes the photo directory."""
    root = photo_dir.resolve()
    candidate = (root / storage_name).resolve()
    if root not in candidate.parents:
        raise ServiceError("Photo not found", 404)
    return candidate


def delete_photo_file(photo_dir: Path, storage_name: str) -> None:
    path = photo_path(photo_dir, storage_name)
    path.unlink(missing_ok=True)
    try:
        path.parent.rmdir()  # only succeeds when the issue folder is empty
    except OSError:
        pass
