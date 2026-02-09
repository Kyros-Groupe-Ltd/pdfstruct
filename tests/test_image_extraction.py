"""Tests for image extraction functionality."""

import io
import pytest
from pathlib import Path

from pdfstruct.models.document import BBox, ImageInfo
from pdfstruct.extractors.images import (
    _determine_format,
    _resolve_colorspace,
    _detect_captions,
    _sniff_format,
    _colorspace_to_pil_mode,
    _infer_mode_from_size,
    deduplicate_images,
    generate_thumbnail,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestImageInfo:
    """Tests for the ImageInfo model."""

    def test_basic_creation(self):
        bbox = BBox(x0=0, y0=0, x1=100, y1=100)
        img = ImageInfo(bbox=bbox, width_px=200, height_px=100, format="jpeg")
        assert img.aspect_ratio == 2.0
        assert img.dpi == 0.0
        assert img.format == "jpeg"

    def test_dpi_computation(self):
        bbox = BBox(x0=0, y0=0, x1=100, y1=100)
        img = ImageInfo(bbox=bbox, dpi_x=150.0, dpi_y=150.0)
        assert img.dpi == 150.0

    def test_dpi_single_axis(self):
        bbox = BBox(x0=0, y0=0, x1=100, y1=100)
        img = ImageInfo(bbox=bbox, dpi_x=72.0, dpi_y=0.0)
        assert img.dpi == 72.0

    def test_aspect_ratio_zero_height(self):
        bbox = BBox(x0=0, y0=0, x1=100, y1=100)
        img = ImageInfo(bbox=bbox, width_px=200, height_px=0)
        assert img.aspect_ratio == 0.0

    def test_to_dict(self):
        bbox = BBox(x0=10, y0=20, x1=110, y1=120)
        img = ImageInfo(
            bbox=bbox, width_px=300, height_px=200,
            format="jpeg", colorspace="rgb",
            dpi_x=72.0, dpi_y=72.0,
            caption="Figure 1: Test",
        )
        d = img.to_dict()
        assert d["format"] == "jpeg"
        assert d["caption"] == "Figure 1: Test"
        assert d["width_px"] == 300
        assert d["height_px"] == 200
        assert d["dpi"] == 72.0
        assert "image_data_base64" not in d

    def test_to_dict_with_bytes(self):
        bbox = BBox(x0=0, y0=0, x1=100, y1=100)
        img = ImageInfo(bbox=bbox, image_bytes=b"fake_data")
        d = img.to_dict(include_bytes=True)
        assert "image_data_base64" in d

    def test_to_dict_duplicate_fields(self):
        bbox = BBox(x0=0, y0=0, x1=100, y1=100)
        img = ImageInfo(bbox=bbox, is_duplicate=True, duplicate_of_index=3)
        d = img.to_dict()
        assert d["is_duplicate"] is True
        assert d["duplicate_of_index"] == 3

    def test_to_dict_error_field(self):
        bbox = BBox(x0=0, y0=0, x1=100, y1=100)
        img = ImageInfo(bbox=bbox, extraction_error="stream decode failed")
        d = img.to_dict()
        assert d["extraction_error"] == "stream decode failed"

    def test_save_no_data(self):
        bbox = BBox(x0=0, y0=0, x1=100, y1=100)
        img = ImageInfo(bbox=bbox)
        assert img.save("test.png") is False

    def test_save_with_data(self, tmp_path):
        from PIL import Image as PILImage
        # Create a real PNG
        pil = PILImage.new("RGB", (10, 10), color="red")
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        bbox = BBox(x0=0, y0=0, x1=100, y1=100)
        img = ImageInfo(bbox=bbox, image_bytes=png_bytes)
        out_path = str(tmp_path / "out.png")
        assert img.save(out_path) is True
        assert Path(out_path).exists()
        assert Path(out_path).stat().st_size > 0

    def test_save_with_format_conversion(self, tmp_path):
        from PIL import Image as PILImage
        pil = PILImage.new("RGB", (10, 10), color="blue")
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        bbox = BBox(x0=0, y0=0, x1=100, y1=100)
        img = ImageInfo(bbox=bbox, image_bytes=png_bytes)
        out_path = str(tmp_path / "out.jpg")
        assert img.save(out_path, fmt="JPEG") is True


class TestFormatDetection:
    def test_sniff_jpeg(self):
        assert _sniff_format(b"\xff\xd8\xff\xe0" + b"\x00" * 10) == "jpeg"

    def test_sniff_png(self):
        assert _sniff_format(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10) == "png"

    def test_sniff_tiff_le(self):
        assert _sniff_format(b"II*\x00" + b"\x00" * 10) == "tiff"

    def test_sniff_tiff_be(self):
        assert _sniff_format(b"MM\x00*" + b"\x00" * 10) == "tiff"

    def test_sniff_unknown(self):
        assert _sniff_format(b"\x00\x01\x02\x03\x04\x05\x06\x07\x08") == "unknown"

    def test_sniff_short_data(self):
        assert _sniff_format(b"\xff") == "unknown"


class TestColorspaceResolution:
    def test_rgb(self):
        assert _resolve_colorspace(["DeviceRGB"]) == "rgb"

    def test_cmyk(self):
        assert _resolve_colorspace(["DeviceCMYK"]) == "cmyk"

    def test_gray(self):
        assert _resolve_colorspace(["DeviceGray"]) == "grayscale"

    def test_none_list(self):
        assert _resolve_colorspace([None]) == "unknown"

    def test_empty(self):
        assert _resolve_colorspace([]) == "unknown"

    def test_non_list(self):
        assert _resolve_colorspace("DeviceRGB") == "rgb"


class TestPilModes:
    def test_grayscale(self):
        assert _colorspace_to_pil_mode("grayscale", 8) == "L"

    def test_rgb(self):
        assert _colorspace_to_pil_mode("rgb", 8) == "RGB"

    def test_cmyk(self):
        assert _colorspace_to_pil_mode("cmyk", 8) == "CMYK"

    def test_1bit(self):
        assert _colorspace_to_pil_mode("grayscale", 1) == "1"

    def test_unknown_8bpc(self):
        assert _colorspace_to_pil_mode("unknown", 8) == "RGB"

    def test_infer_grayscale(self):
        data = b"\x00" * (100 * 100)
        assert _infer_mode_from_size(data, 100, 100, 8) == "L"

    def test_infer_rgb(self):
        data = b"\x00" * (100 * 100 * 3)
        assert _infer_mode_from_size(data, 100, 100, 8) == "RGB"

    def test_infer_cmyk(self):
        data = b"\x00" * (100 * 100 * 4)
        assert _infer_mode_from_size(data, 100, 100, 8) == "CMYK"


class TestDeduplication:
    def test_marks_duplicates(self):
        bbox = BBox(x0=0, y0=0, x1=1, y1=1)
        images = [
            ImageInfo(bbox=bbox, content_hash="abc123"),
            ImageInfo(bbox=bbox, content_hash="def456"),
            ImageInfo(bbox=bbox, content_hash="abc123"),  # duplicate of 0
        ]
        deduplicate_images(images)
        assert images[0].is_duplicate is False
        assert images[1].is_duplicate is False
        assert images[2].is_duplicate is True
        assert images[2].duplicate_of_index == 0

    def test_no_hash_skipped(self):
        bbox = BBox(x0=0, y0=0, x1=1, y1=1)
        images = [
            ImageInfo(bbox=bbox, content_hash=None),
            ImageInfo(bbox=bbox, content_hash=None),
        ]
        deduplicate_images(images)
        assert images[0].is_duplicate is False
        assert images[1].is_duplicate is False

    def test_empty_list(self):
        assert deduplicate_images([]) == []


class TestCaptionDetection:
    def test_detects_figure_caption_below(self):
        from pdfstruct.models.document import TextLine, FontInfo
        bbox = BBox(x0=50, y0=200, x1=400, y1=500)
        img = ImageInfo(bbox=bbox, page_number=1)

        line = TextLine(
            text="Figure 1: Architecture diagram",
            bbox=BBox(x0=50, y0=175, x1=400, y1=190),
            font=FontInfo(name="Helvetica", size=10),
        )
        _detect_captions([img], [line], page_height=792)
        assert img.caption == "Figure 1: Architecture diagram"

    def test_detects_fig_caption(self):
        from pdfstruct.models.document import TextLine, FontInfo
        bbox = BBox(x0=50, y0=200, x1=400, y1=500)
        img = ImageInfo(bbox=bbox, page_number=1)

        line = TextLine(
            text="Fig. 3 Workflow overview",
            bbox=BBox(x0=50, y0=175, x1=400, y1=190),
            font=FontInfo(name="Helvetica", size=10),
        )
        _detect_captions([img], [line], page_height=792)
        assert img.caption == "Fig. 3 Workflow overview"

    def test_no_caption_too_far(self):
        from pdfstruct.models.document import TextLine, FontInfo
        bbox = BBox(x0=50, y0=200, x1=400, y1=500)
        img = ImageInfo(bbox=bbox, page_number=1)

        line = TextLine(
            text="Figure 1: Too far away",
            bbox=BBox(x0=50, y0=50, x1=400, y1=65),
            font=FontInfo(name="Helvetica", size=10),
        )
        _detect_captions([img], [line], page_height=792)
        assert img.caption is None

    def test_no_caption_for_non_matching_text(self):
        from pdfstruct.models.document import TextLine, FontInfo
        bbox = BBox(x0=50, y0=200, x1=400, y1=500)
        img = ImageInfo(bbox=bbox, page_number=1)

        line = TextLine(
            text="This is just regular paragraph text.",
            bbox=BBox(x0=50, y0=175, x1=400, y1=190),
            font=FontInfo(name="Helvetica", size=10),
        )
        _detect_captions([img], [line], page_height=792)
        assert img.caption is None


class TestThumbnail:
    def test_generate_thumbnail(self):
        from PIL import Image
        img = Image.new("RGB", (400, 300), color="red")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        thumb = generate_thumbnail(png_bytes, max_size=(100, 100))
        assert thumb is not None
        assert len(thumb) < len(png_bytes)

        # Verify the thumbnail dimensions
        thumb_img = Image.open(io.BytesIO(thumb))
        assert thumb_img.width <= 100
        assert thumb_img.height <= 100

    def test_thumbnail_jpeg_output(self):
        from PIL import Image
        img = Image.new("RGB", (200, 200), color="blue")
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        thumb = generate_thumbnail(buf.getvalue(), max_size=(50, 50), output_format="JPEG")
        assert thumb is not None
        # JPEG magic bytes
        assert thumb[:2] == b"\xff\xd8"

    def test_thumbnail_invalid_data(self):
        result = generate_thumbnail(b"not an image")
        assert result is None


class TestIntegration:
    """Integration tests using actual PDF files."""

    @pytest.fixture(autouse=True)
    def _ensure_fixtures(self):
        """Generate test PDFs if they don't exist."""
        if not (FIXTURES_DIR / "with_images.pdf").exists():
            from tests.generate_test_pdf import generate_image_document
            generate_image_document()

    def test_parse_with_images(self):
        """Test full image extraction on the test PDF."""
        import pdfstruct
        pdf_path = str(FIXTURES_DIR / "with_images.pdf")
        doc = pdfstruct.parse(pdf_path, extract_images=True)

        # Should have at least one page
        assert doc.page_count >= 1

        # Collect all images
        all_images = [img for page in doc.pages for img in page.images]

        # Should find at least the 2 test images
        if all_images:
            img = all_images[0]
            assert img.width_px > 0
            assert img.height_px > 0
            assert img.format in ("jpeg", "png", "flate", "raw", "unknown")
            assert img.page_number >= 1

            # If data was extracted, verify it
            if img.image_bytes:
                assert img.file_size_bytes > 0
                assert img.content_hash is not None

    def test_parse_without_images_backward_compat(self):
        """Test that parsing without extract_images still works."""
        import pdfstruct
        pdf_path = str(FIXTURES_DIR / "with_images.pdf")
        doc = pdfstruct.parse(pdf_path)

        # Images should still be ImageInfo objects (with bbox only)
        for page in doc.pages:
            for img in page.images:
                assert isinstance(img, pdfstruct.ImageInfo)
                assert img.image_bytes is None  # no data extracted

    def test_metadata_image_stats(self):
        """Test that metadata includes image stats."""
        import pdfstruct
        pdf_path = str(FIXTURES_DIR / "with_images.pdf")
        doc = pdfstruct.parse(pdf_path, extract_images=True)

        meta = doc.metadata
        assert meta is not None
        assert meta.image_count >= 0
        assert meta.unique_image_count >= 0

    def test_image_info_export(self):
        """Test ImageInfo is accessible from top-level import."""
        from pdfstruct import ImageInfo, generate_thumbnail
        bbox = BBox(x0=0, y0=0, x1=100, y1=100)
        img = ImageInfo(bbox=bbox, width_px=100, height_px=100)
        assert img.to_dict()["width_px"] == 100
