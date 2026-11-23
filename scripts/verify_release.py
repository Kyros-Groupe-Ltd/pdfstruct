"""Release gates for pdfstructx; no network or publishing operations.

Version/TOML checks use Python 3.11+ (release CI uses 3.12). Installed-wheel
and OCR smoke checks also run on the package's Python 3.10 baseline.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import shutil
import tarfile
import zipfile
from email.parser import BytesParser
from importlib import metadata
from pathlib import Path

PROJECT_NAME = "pdfstructx"
ROOT = Path(__file__).resolve().parents[1]


def module_version(source: str) -> str:
    """Read a literal version without importing package/dependency code."""
    for statement in ast.parse(source).body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in statement.targets
        ):
            value = ast.literal_eval(statement.value)
            if isinstance(value, str) and value:
                return value
    raise ValueError("__version__ must be a nonempty literal string")


def source_version(root: Path = ROOT, tag: str = "") -> str:
    """Reject inconsistent source versions and tags before building a release."""
    try:
        import tomllib
    except ImportError as exc:
        raise ValueError("Source version checks require Python 3.11+ (CI uses 3.12)") from exc

    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    if project["name"] != PROJECT_NAME or not isinstance(version, str) or not version:
        raise ValueError("Expected project pdfstructx with a nonempty static version")
    runtime_version = module_version(
        (root / "src/pdfstruct/__init__.py").read_text(encoding="utf-8")
    )
    if runtime_version != version:
        raise ValueError(f"Source versions differ: pyproject={version}, __init__={runtime_version}")
    if tag and tag != f"v{version}":
        raise ValueError(f"Release tag {tag!r} must equal v{version}")
    return version


def _check_metadata(raw: bytes, expected_version: str, artifact: Path) -> None:
    package = BytesParser().parsebytes(raw)
    name = re.sub(r"[-_.]+", "-", package.get("Name", "")).lower()
    if name != PROJECT_NAME or package.get("Version") != expected_version:
        raise ValueError(f"Wrong name/version in {artifact.name}: {name} {package.get('Version')}")


def validate_distributions(directory: Path, version: str) -> None:
    """Check the exact wheel/sdist pair without extracting either archive."""
    files = sorted(path for path in directory.iterdir() if path.is_file())
    wheels = [path for path in files if path.suffix == ".whl"]
    sdists = [path for path in files if path.name.endswith(".tar.gz")]
    if len(files) != 2 or len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("dist/ must contain exactly one wheel and one .tar.gz source distribution")

    with zipfile.ZipFile(wheels[0]) as wheel:
        names = wheel.namelist()
        metadata_paths = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_paths) != 1:
            raise ValueError("Wheel must contain exactly one METADATA file")
        _check_metadata(wheel.read(metadata_paths[0]), version, wheels[0])
        for name in (
            "pdfstruct/__init__.py", "pdfstruct/parser.py", "pdfstruct/extractors/ocr.py",
            "pdfstruct/ingestion.py", "pdfstruct/structure/blocks.py",
        ):
            if name not in names:
                raise ValueError(f"Wheel is missing {name}")
        if module_version(wheel.read("pdfstruct/__init__.py").decode("utf-8")) != version:
            raise ValueError("Wheel __version__ disagrees with release metadata")

    with tarfile.open(sdists[0], "r:gz") as sdist:
        prefix = f"{PROJECT_NAME}-{version}/"
        required = (
            "PKG-INFO", "pyproject.toml", "LICENSE", "docs/ocr.md", "docs/ingestion.md",
            "docs/releasing.md", "scripts/verify_release.py", "src/pdfstruct/__init__.py",
            "src/pdfstruct/extractors/ocr.py", "src/pdfstruct/ingestion.py",
        )
        for name in required:
            try:
                member = sdist.getmember(prefix + name)
            except KeyError as exc:
                raise ValueError(f"Source distribution is missing {name}") from exc
            if not member.isfile():
                raise ValueError(f"Source distribution entry is not a regular file: {name}")
        metadata_file = sdist.extractfile(prefix + "PKG-INFO")
        assert metadata_file is not None
        with metadata_file:
            _check_metadata(metadata_file.read(), version, sdists[0])
        init_file = sdist.extractfile(prefix + "src/pdfstruct/__init__.py")
        assert init_file is not None
        with init_file:
            if module_version(init_file.read().decode("utf-8")) != version:
                raise ValueError("Source distribution __version__ disagrees with release metadata")


def installed_smoke(expected_version: str, pdf: Path, documents: bool = False) -> None:
    """Verify the installed distribution, rather than an editable source tree."""
    import pdfstruct

    installed = metadata.distribution(PROJECT_NAME)
    direct_url = installed.read_text("direct_url.json")
    if direct_url and json.loads(direct_url).get("dir_info", {}).get("editable"):
        raise ValueError("Smoke check requires a built wheel, not an editable installation")
    installed_file = installed.locate_file("pdfstruct/__init__.py").resolve()
    if Path(pdfstruct.__file__).resolve() != installed_file:
        raise ValueError("Imported pdfstruct is shadowing the installed distribution")
    if installed.version != expected_version or pdfstruct.__version__ != expected_version:
        raise ValueError(f"Installed wheel must report version {expected_version}")
    document = pdfstruct.parse(pdf)
    if document.page_count < 1 or not document.text.strip():
        raise ValueError("Installed wheel did not extract the native PDF fixture")
    json.loads(pdfstruct.to_json(document))
    if not pdfstruct.to_markdown(document).strip() or not pdfstruct.to_text(document).strip():
        raise ValueError("Installed wheel export smoke check failed")
    pdfstruct.PDFParser(ocr="auto")
    if not issubclass(pdfstruct.OCRDependencyError, Exception):
        raise TypeError("OCR public exception is unavailable")
    text = pdfstruct.parse_document("Québec français".encode(), filename="smoke.txt")
    if text.text != "Québec français" or text.blocks[0].locator.get("line_start") != 1:
        raise ValueError("Installed wheel TXT ingestion/anchor smoke check failed")
    if documents:
        from docx import Document

        word = Document()
        word.add_heading("Programme Québec", 1)
        word.add_table(rows=1, cols=1).cell(0, 0).text = "250 $"
        buffer = io.BytesIO()
        word.save(buffer)
        parsed_word = pdfstruct.parse_document(buffer.getvalue(), filename="smoke.docx")
        if [block.kind for block in parsed_word.blocks] != ["heading", "table"]:
            raise ValueError("Installed wheel DOCX ingestion smoke check failed")
        if parsed_word.blocks[1].rows != [["250 $"]]:
            raise ValueError("Installed wheel DOCX table smoke check failed")
    print(f"Installed wheel {installed.version}: native PDF, TXT, exports and OCR API passed")
    if documents:
        print("Installed wheel: DOCX heading/table ingestion passed")


def ocr_smoke() -> None:
    """Require real engines and French recognition; missing tools fail, never skip."""
    import onnxruntime  # noqa: F401
    import pypdfium2  # noqa: F401
    import pytesseract
    import rapidocr  # noqa: F401
    from PIL import Image, ImageDraw, ImageFont
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    import pdfstruct

    if not shutil.which("tesseract"):
        raise ValueError("Tesseract executable is required for the OCR release gate")
    missing = {"eng", "fra"} - set(pytesseract.get_languages(config=""))
    if missing:
        raise ValueError(f"Missing Tesseract language packs: {sorted(missing)}")
    font = ImageFont.truetype("DejaVuSans.ttf", 40)
    image = Image.new("RGB", (1600, 800), "white")
    try:
        draw = ImageDraw.Draw(image)
        draw.text((80, 160), "Facture Québec français 12345", fill="black", font=font)
        draw.text((80, 240), "Montant total 250 dollars", fill="black", font=font)
        output = io.BytesIO()
        pdf = canvas.Canvas(output, pagesize=(800, 400))
        pdf.drawImage(ImageReader(image), 0, 0, width=800, height=400)
        pdf.showPage()
        pdf.save()
        for backend in ("rapidocr", "tesseract"):
            document = pdfstruct.parse(
                output.getvalue(), ocr=True, ocr_backend=backend,
                ocr_language="fra+eng", ocr_dpi=200,
            )
            for expected in ("Québec", "français", "12345", "250"):
                if expected not in document.text:
                    raise ValueError(f"{backend} French smoke check missed {expected!r}")
            if document.metadata.ocr_page_count != 1:
                raise ValueError(f"{backend} did not report OCR provenance")
            print(f"{backend}: real French OCR and provenance passed")
    finally:
        image.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    version = commands.add_parser("version")
    version.add_argument("--tag", default="")
    distributions = commands.add_parser("distributions")
    distributions.add_argument("--directory", type=Path, default=ROOT / "dist")
    smoke = commands.add_parser("smoke")
    smoke.add_argument("--expected-version", required=True)
    smoke.add_argument("--pdf", type=Path, required=True)
    smoke.add_argument("--documents", action="store_true", help="Require the DOCX extra")
    commands.add_parser("ocr-smoke")
    args = parser.parse_args()
    try:
        if args.command == "version":
            print(f"version={source_version(tag=args.tag)}")
        elif args.command == "distributions":
            validate_distributions(args.directory, source_version())
            print("Wheel and source distribution contents/version checks passed")
        elif args.command == "smoke":
            installed_smoke(args.expected_version, args.pdf, args.documents)
        else:
            ocr_smoke()
    except (ValueError, OSError, ImportError, KeyError, RuntimeError, TypeError) as exc:
        parser.exit(1, f"Release check failed: {exc}\n")


if __name__ == "__main__":
    main()
