"""Release regressions: reject mismatched tags, stale distributions and missing code."""

import importlib.util
import io
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "verify_release", Path(__file__).resolve().parents[1] / "scripts/verify_release.py"
)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def make_source(root, project_version="0.3.0", runtime_version="0.3.0"):
    (root / "src/pdfstruct").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "pdfstructx"\nversion = "{project_version}"\n', encoding="utf-8"
    )
    (root / "src/pdfstruct/__init__.py").write_text(
        f'__version__ = "{runtime_version}"\n', encoding="utf-8"
    )


def make_distributions(root, version="0.3.0", wheel_version=None, missing_ocr=False):
    root.mkdir(exist_ok=True)
    wheel_version = wheel_version or version
    with zipfile.ZipFile(root / f"pdfstructx-{version}-py3-none-any.whl", "w") as wheel:
        wheel.writestr(
            f"pdfstructx-{version}.dist-info/METADATA",
            f"Name: pdfstructx\nVersion: {wheel_version}\n",
        )
        wheel.writestr("pdfstruct/__init__.py", f'__version__ = "{version}"\n')
        wheel.writestr("pdfstruct/parser.py", "")
        wheel.writestr("pdfstruct/ingestion.py", "")
        wheel.writestr("pdfstruct/structure/blocks.py", "")
        if not missing_ocr:
            wheel.writestr("pdfstruct/extractors/ocr.py", "")
    files = {
        "PKG-INFO": f"Name: pdfstructx\nVersion: {version}\n",
        "pyproject.toml": "",
        "LICENSE": "",
        "docs/ocr.md": "",
        "docs/ingestion.md": "",
        "docs/releasing.md": "",
        "scripts/verify_release.py": "",
        "src/pdfstruct/__init__.py": f'__version__ = "{version}"\n',
        "src/pdfstruct/extractors/ocr.py": "",
        "src/pdfstruct/ingestion.py": "",
    }
    with tarfile.open(root / f"pdfstructx-{version}.tar.gz", "w:gz") as sdist:
        for name, content in files.items():
            raw = content.encode("utf-8")
            member = tarfile.TarInfo(f"pdfstructx-{version}/{name}")
            member.size = len(raw)
            sdist.addfile(member, io.BytesIO(raw))


@pytest.mark.skipif(sys.version_info < (3, 11), reason="Release TOML gate uses Python 3.11+")
@pytest.mark.parametrize("tag", ["", "v0.3.0"])
def test_matching_source_versions_and_tag(tmp_path, tag):
    make_source(tmp_path)
    assert release.source_version(tmp_path, tag) == "0.3.0"


@pytest.mark.skipif(sys.version_info < (3, 11), reason="Release TOML gate uses Python 3.11+")
def test_source_version_mismatch_is_rejected(tmp_path):
    make_source(tmp_path, runtime_version="0.2.4")
    with pytest.raises(ValueError, match="Source versions differ"):
        release.source_version(tmp_path)


@pytest.mark.skipif(sys.version_info < (3, 11), reason="Release TOML gate uses Python 3.11+")
@pytest.mark.parametrize("tag", ["v0.2.4", "v0.3.0-old", "0.3.0"])
def test_wrong_tag_is_rejected(tmp_path, tag):
    make_source(tmp_path)
    with pytest.raises(ValueError, match="must equal v0.3.0"):
        release.source_version(tmp_path, tag)


def test_distribution_pair_passes(tmp_path):
    make_distributions(tmp_path)
    release.validate_distributions(tmp_path, "0.3.0")


def test_old_wheel_metadata_is_rejected(tmp_path):
    make_distributions(tmp_path, wheel_version="0.2.4")
    with pytest.raises(ValueError, match="Wrong name/version"):
        release.validate_distributions(tmp_path, "0.3.0")


def test_missing_ocr_module_is_rejected(tmp_path):
    make_distributions(tmp_path, missing_ocr=True)
    with pytest.raises(ValueError, match="missing pdfstruct/extractors/ocr.py"):
        release.validate_distributions(tmp_path, "0.3.0")


def test_stale_extra_distribution_is_rejected(tmp_path):
    make_distributions(tmp_path)
    (tmp_path / "pdfstructx-0.2.4-py3-none-any.whl").write_bytes(b"old build")
    with pytest.raises(ValueError, match="exactly one wheel"):
        release.validate_distributions(tmp_path, "0.3.0")


def test_reading_version_does_not_execute_source():
    source = 'raise RuntimeError("must not run")\n__version__ = "0.3.0"\n'
    assert release.module_version(source) == "0.3.0"
