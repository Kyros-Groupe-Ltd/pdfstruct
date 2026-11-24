# Releasing pdfstructx 0.3.0

The package installed from PyPI is **pdfstructx**; its Python import is **pdfstruct**.
This guide describes publishing the tested 0.3.0 code. A GitHub branch or merged
pull request does not update the package users install from PyPI.

The existing 0.2.4 files cannot be replaced in place. Use the new version 0.3.0 in
both `pyproject.toml` and `src/pdfstruct/__init__.py`, and the tag `v0.3.0`.
PyPI reserves previously used distribution filenames, including deleted files
([PyPI upload troubleshooting](https://docs.pypi.org/publishing/troubleshooting/)).

## What the workflow does

`.github/workflows/publish.yml` runs on pull requests to `main`, pushes to `main`,
and `v*` tag pushes. Only a tag push can reach the production publishing job.

1. Check that the two source versions agree. On a tag run, require the tag to be
   exactly `v` followed by that version.
2. Build a wheel and source distribution with `python -m build`; check their
   descriptions/metadata with Twine and inspect required package contents.
3. Save that one wheel/sdist pair as the workflow's `release-distributions` artifact.
4. Install the saved wheel, not an editable checkout, on Python 3.10 through 3.13.
   Check the installed version/public API, parse native PDF, French TXT and DOCX
   fixtures, and run the base/document ingestion tests with the `documents` extra.
5. Install the same wheel with OCR extras in a separate job. Require the actual
   RapidOCR and Tesseract engines, the English/French Tesseract language packs, and
   successful French text recognition before running the complete suite with all
   extras. This also exercises geometry tests that skip without the renderer.
6. After every job succeeds, a tag run downloads and publishes the same saved
   distributions. Nothing is rebuilt in the publishing job.

The workflow grants `contents: read` by default. Only the final publishing job
receives `id-token: write`, and it has just two steps: retrieve artifacts and upload
them. This follows the separation described in
[PyPI's Trusted Publishing security model](https://docs.pypi.org/trusted-publishers/security-model/).

The French fixture verifies an actual OCR execution path and accent preservation.
It is a regression gate, not an accuracy benchmark for arbitrary French documents,
handwriting, scanned tables or a complete document-processing application.

## One-time PyPI Trusted Publisher setup

In the **existing** `pdfstructx` project on PyPI, open **Manage → Publishing**:

<https://pypi.org/manage/project/pdfstructx/settings/publishing/>

The current workflow expects these exact GitHub publisher fields:

| PyPI field | Value |
| --- | --- |
| Owner | `Kyros-Groupe-Ltd` |
| Repository name | `pdfstruct` |
| Workflow filename | `publish.yml` |
| Environment name | Leave empty for this workflow |

The workflow filename is the basename, not `.github/workflows/publish.yml`.
No PyPI API token, username or password belongs in the workflow. GitHub's OIDC
identity supplies short-lived publishing credentials
([adding a publisher](https://docs.pypi.org/trusted-publishers/adding-a-publisher/),
[using a publisher](https://docs.pypi.org/trusted-publishers/using-a-publisher/)).

This update deliberately keeps the previous workflow's **no-environment** identity.
It does not create a new GitHub environment or require a new approval step. The
existing PyPI account configuration cannot be verified from repository files; check
that its registered publisher matches the table. If the current publisher instead
requires an environment such as `pypi`, reconcile that setting with the workflow
before tagging. Adding an environment is an optional coordinated hardening change:
the job and PyPI publisher must then use the same exact name.

## Test locally before the release tag

Use Python 3.12 for the release tooling. The library still supports Python 3.10+;
the source-version checker uses Python 3.11's standard-library TOML parser.
These commands assume a Bash shell at the repository root (Linux, macOS or WSL).
On Windows, a virtual environment's interpreter is under `Scripts/python.exe`
instead of `bin/python`.

```bash
python3.12 -m venv .venv-release
.venv-release/bin/python -m pip install --upgrade pip build twine
.venv-release/bin/python scripts/verify_release.py version --tag v0.3.0

# Remove only old generated distributions before rebuilding.
.venv-release/bin/python -c "import shutil; shutil.rmtree('dist', ignore_errors=True)"
.venv-release/bin/python -m build
.venv-release/bin/python -m twine check --strict dist/*
.venv-release/bin/python scripts/verify_release.py distributions
.venv-release/bin/python -m pip install './dist/pdfstructx-0.3.0-py3-none-any.whl[dev,documents]'
.venv-release/bin/python -I scripts/verify_release.py smoke --expected-version 0.3.0 --pdf tests/fixtures/structured.pdf --documents
.venv-release/bin/python -m pytest -m 'not ocr_integration' --tb=short -q
```

For actual OCR testing on Ubuntu/Debian:

```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng tesseract-ocr-fra fonts-dejavu-core
.venv-release/bin/python -m pip install './dist/pdfstructx-0.3.0-py3-none-any.whl[dev,documents,ocr,tesseract]'
.venv-release/bin/python -I scripts/verify_release.py ocr-smoke
.venv-release/bin/python -m pytest --tb=short -q
```

Do not install the project editable in this environment: the wheel smoke check
intentionally rejects editable installs. Use a fresh virtual environment after
code changes, or uninstall `pdfstructx` and reinstall the newly built wheel.
OCR model acquisition may require network access on first use. For an offline
deployment, provision and verify the models in advance as described in `ocr.md`.

Keep `.venv-release/`, generated distributions and private evaluation documents out
of commits. CI independently rebuilds and tests the code at the release tag; a
local build is a rehearsal, not an artifact implicitly substituted into CI.

## Publish the tested release

Merge the reviewed changes into `main`, including both 0.3.0 version declarations,
then wait for that commit's **CI & Publish** run to pass. Production publication is
triggered by pushing the new tag:

```bash
git switch main
git pull --ff-only origin main
git status --short
python3.12 scripts/verify_release.py version --tag v0.3.0
git tag -a v0.3.0 -m 'pdfstructx 0.3.0: local OCR and structured document ingestion'
git push origin v0.3.0
```

Run the tag commands from a clean checkout of the intended merged commit. The
tag run repeats all gates before uploading. Creating a GitHub Release is optional;
it is not the publishing trigger. A branch push or pull request alone does not
publish anything. These instructions are a release procedure; preparing the code
does not itself execute them.

If tests fail, fix them before publication. If publishing alone fails before any
file uploads, correct the publisher configuration and rerun the failed job so it
reuses the saved, tested artifacts. If files already reached PyPI, inspect the
release before retrying: the workflow intentionally does not use `skip-existing`.
Do not replace an already published release with different code. Fixes after
0.3.0 publication need a new version/tag, normally 0.3.1 / `v0.3.1`.

## Verify the package that users receive

After the tag workflow succeeds, open:

<https://pypi.org/project/pdfstructx/0.3.0/>

In a fresh environment, install from production PyPI and check both versions:

```bash
python3.12 -m venv .venv-pypi-check
.venv-pypi-check/bin/python -m pip install --index-url https://pypi.org/simple 'pdfstructx==0.3.0'
.venv-pypi-check/bin/python -c "import pdfstruct; from importlib.metadata import version; print(version('pdfstructx'), pdfstruct.__version__); assert version('pdfstructx') == pdfstruct.__version__ == '0.3.0'"
.venv-pypi-check/bin/python -I scripts/verify_release.py smoke --expected-version 0.3.0 --pdf tests/fixtures/structured.pdf
```

Users wanting OCR install `pdfstructx[ocr]==0.3.0` or
`pdfstructx[tesseract]==0.3.0`; the latter also needs the system Tesseract executable
and the requested language packs. Installing an extra does not turn OCR on:
call `pdfstruct.parse(..., ocr=True, ...)` as shown in `ocr.md`.
DOCX ingestion requires `pdfstructx[documents]==0.3.0`; PDF and TXT ingestion do
not require that extra. Install `pdfstructx[documents,ocr,tesseract]==0.3.0` to
combine all three optional features. See `ingestion.md` for the canonical API.

## Optional TestPyPI rehearsal

TestPyPI is a separate service, account database and publisher configuration; a
production PyPI account or Trusted Publisher does not configure it automatically
([TestPyPI guide](https://packaging.python.org/en/latest/guides/using-testpypi/)).
The checked-in workflow publishes only to production PyPI on tags. TestPyPI is not
an additional mandatory gate and is not silently enabled by this change.

For a local rehearsal, use a separate TestPyPI account/token and the distributions
that passed the local checks:

```bash
.venv-release/bin/python -m twine upload --repository testpypi dist/*
```

Supply the TestPyPI token at Twine's prompt, not in source files or shell history.
The upload endpoint is `https://test.pypi.org/legacy/`. If you later automate this
with OIDC, register a **separate TestPyPI** Trusted Publisher matching the actual
owner, repository, workflow filename and optional environment, then use that
endpoint in a separate publishing job. Do not change the production job's target
as a casual test.

Verify the package without mixing dependency indexes:

```bash
python3.12 -m venv .venv-testpypi
.venv-testpypi/bin/python -m pip install --index-url https://pypi.org/simple 'pdfminer.six>=20231228' 'Pillow>=10.0.0'
.venv-testpypi/bin/python -m pip install --index-url https://test.pypi.org/simple --no-deps 'pdfstructx==0.3.0'
.venv-testpypi/bin/python -I scripts/verify_release.py smoke --expected-version 0.3.0 --pdf tests/fixtures/structured.pdf
```

Install dependencies from production PyPI first, then the exact test package with
`--no-deps` from TestPyPI. Do not use `--extra-index-url` to mix the two catalogs.
A successful TestPyPI upload does not publish to production or reserve the version
there. TestPyPI also disallows overwriting an uploaded filename; use a new test
version for a changed test build.
