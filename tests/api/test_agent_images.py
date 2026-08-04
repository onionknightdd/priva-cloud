import base64

import pytest
from fastapi import HTTPException

from priva_agent_runner.routers.agent import _MAX_IMAGE_SIZE, _validate_images
from priva_common.models.agent import ImageItem


def _image(data: bytes, media_type: str = "image/png") -> ImageItem:
    return ImageItem(
        data=base64.b64encode(data).decode("ascii"),
        media_type=media_type,
    )


def test_image_at_five_mib_boundary_is_accepted() -> None:
    assert _MAX_IMAGE_SIZE == 5 * 1024 * 1024
    item = _image(b"\0" * _MAX_IMAGE_SIZE)

    assert _validate_images([item]) == [
        {"data": item.data, "media_type": "image/png"}
    ]


def test_image_over_five_mib_boundary_is_rejected() -> None:
    with pytest.raises(HTTPException) as exc:
        _validate_images([_image(b"\0" * (_MAX_IMAGE_SIZE + 1))])

    assert exc.value.status_code == 413
    assert "5MB" in exc.value.detail


def test_malformed_base64_image_is_rejected() -> None:
    with pytest.raises(HTTPException) as exc:
        _validate_images([ImageItem(data="not-base64!", media_type="image/png")])

    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid base64 image data"
