import respx
from httpx import Response

from app.media import archive_photo, download_media


@respx.mock
async def test_download_media_returns_bytes():
    respx.get("https://cdn.linqapp.com/x.jpg").mock(
        return_value=Response(200, content=b"fakejpegbytes")
    )
    data = await download_media("https://cdn.linqapp.com/x.jpg")
    assert data == b"fakejpegbytes"


def test_archive_photo_uploads_and_returns_path():
    path = archive_photo("test-item-id", b"fakejpegbytes")
    assert path == "test-item-id.jpg"
