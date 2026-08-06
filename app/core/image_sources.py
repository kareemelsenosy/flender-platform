"""Fetch Image-Sorter inputs from the links brands actually send us.

Brands hand over their season's photos in one of a handful of ways, and the
Image Sorter accepts all of them so nobody has to download and re-upload:

* **Dropbox** shared folder or file link — Dropbox serves any shared folder as
  a ZIP when asked with ``dl=1``.
* **Google Drive** file link — either a ZIP of photos or a catalogue PDF, whose
  embedded images we pull out page by page.
* **Brandboom** presentation link — best-effort scrape of the public share page.
* **Google Sheets** link — used for the *master file*, exported as ``.xlsx`` so
  it lands in the same parser as an uploaded workbook.

Anything that can't be fetched raises :class:`SourceFetchError` with a message
that tells the user what to do instead (almost always: download it yourself and
upload the ZIP).
"""
from __future__ import annotations

import io
import logging
import os
import re
import shutil
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests

logger = logging.getLogger(__name__)

MAX_DOWNLOAD_BYTES = 3 * 1024 * 1024 * 1024   # 3 GB — season folders get big
DOWNLOAD_TIMEOUT = 60
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) FLENDER-ImageSorter/1.0"


class SourceFetchError(Exception):
    """A link could not be turned into usable files."""


def detect_source(url: str) -> str:
    """Classify a link: dropbox | gdrive | gsheet | brandboom | direct."""
    host = (urlparse(str(url or "").strip()).netloc or "").lower()
    if "dropbox.com" in host:
        return "dropbox"
    if "docs.google.com" in host:
        return "gsheet"
    if "drive.google.com" in host or "drive.usercontent.google.com" in host:
        return "gdrive"
    if "brandboom.com" in host:
        return "brandboom"
    if host:
        return "direct"
    return ""


# ── Generic download ─────────────────────────────────────────────────────────

def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": _UA})
    return session


def _stream_to_file(response: requests.Response, dest: Path) -> int:
    """Write a streamed response to disk, refusing anything over the size cap."""
    total = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as out:
        for chunk in response.iter_content(chunk_size=1 << 20):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_DOWNLOAD_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                raise SourceFetchError(
                    f"That link is larger than {MAX_DOWNLOAD_BYTES // (1024**3)} GB. "
                    "Split it into smaller folders, or upload a ZIP instead."
                )
            out.write(chunk)
    return total


def _filename_from_response(response: requests.Response, fallback: str) -> str:
    disposition = response.headers.get("content-disposition") or ""
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', disposition)
    if m:
        name = os.path.basename(m.group(1).strip())
        if name:
            return name
    path_name = os.path.basename(urlparse(response.url).path)
    return path_name or fallback


# ── Dropbox ──────────────────────────────────────────────────────────────────

def _dropbox_download_url(url: str) -> str:
    """Rewrite any Dropbox share link to its direct-download form."""
    parts = urlparse(url)
    query = parse_qs(parts.query)
    query["dl"] = ["1"]
    query.pop("e", None)
    query.pop("st", None)
    return urlunparse(parts._replace(query=urlencode(query, doseq=True)))


def fetch_dropbox(url: str, dest_dir: Path) -> list[Path]:
    """Download a Dropbox file or folder link. Folders arrive as a ZIP."""
    download_url = _dropbox_download_url(url)
    try:
        with _session() as session:
            resp = session.get(download_url, stream=True, timeout=DOWNLOAD_TIMEOUT, allow_redirects=True)
            if resp.status_code == 404:
                raise SourceFetchError(
                    "Dropbox returned 404 — the link is private or has expired. "
                    "Re-share it with 'Anyone with the link'."
                )
            resp.raise_for_status()
            content_type = (resp.headers.get("content-type") or "").lower()
            if "text/html" in content_type:
                raise SourceFetchError(
                    "Dropbox served its web page instead of the files, which means the "
                    "link needs a login. Re-share it with 'Anyone with the link', or "
                    "download the folder and upload the ZIP here."
                )
            name = _filename_from_response(resp, "dropbox_download.zip")
            dest = dest_dir / name
            _stream_to_file(resp, dest)
    except SourceFetchError:
        raise
    except requests.RequestException as e:
        raise SourceFetchError(f"Could not download from Dropbox: {e}") from e
    return [dest]


# ── Google Drive ─────────────────────────────────────────────────────────────

def _gdrive_file_id(url: str) -> str:
    for pattern in (r"/file/d/([a-zA-Z0-9_-]{10,})", r"[?&]id=([a-zA-Z0-9_-]{10,})"):
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    raise SourceFetchError("That Google Drive link has no file id in it.")


def fetch_gdrive(url: str, dest_dir: Path) -> list[Path]:
    """Download a shared Google Drive file (ZIP, PDF or image)."""
    file_id = _gdrive_file_id(url)
    endpoint = "https://drive.usercontent.google.com/download"
    try:
        with _session() as session:
            resp = session.get(
                endpoint,
                params={"id": file_id, "export": "download", "confirm": "t"},
                stream=True, timeout=DOWNLOAD_TIMEOUT, allow_redirects=True,
            )
            resp.raise_for_status()
            content_type = (resp.headers.get("content-type") or "").lower()
            if "text/html" in content_type:
                # Drive shows an interstitial for private files and for the
                # virus-scan warning on very large ones.
                raise SourceFetchError(
                    "Google Drive would not serve that file directly — it is either "
                    "private or too large for a direct download. Share it with "
                    "'Anyone with the link', or download it and upload it here."
                )
            name = _filename_from_response(resp, f"gdrive_{file_id}")
            dest = dest_dir / name
            _stream_to_file(resp, dest)
    except SourceFetchError:
        raise
    except requests.RequestException as e:
        raise SourceFetchError(f"Could not download from Google Drive: {e}") from e
    return [dest]


# ── Google Sheets (master file) ──────────────────────────────────────────────

def fetch_google_sheet_xlsx(url: str, dest_dir: Path) -> Path:
    """Export a Google Sheet to ``.xlsx`` so the master-file parser can read it."""
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    if not m:
        raise SourceFetchError("That does not look like a Google Sheets link.")
    sheet_id = m.group(1)
    export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"
    try:
        with _session() as session:
            resp = session.get(export_url, stream=True, timeout=DOWNLOAD_TIMEOUT, allow_redirects=True)
            if resp.status_code in (401, 403) or "text/html" in (resp.headers.get("content-type") or "").lower():
                raise SourceFetchError(
                    "That Google Sheet is not readable by link. Share it with "
                    "'Anyone with the link — Viewer', or download it as .xlsx and upload it."
                )
            resp.raise_for_status()
            dest = dest_dir / f"master_{sheet_id}.xlsx"
            _stream_to_file(resp, dest)
    except SourceFetchError:
        raise
    except requests.RequestException as e:
        raise SourceFetchError(f"Could not export the Google Sheet: {e}") from e
    return dest


# ── Brandboom ────────────────────────────────────────────────────────────────

_BRANDBOOM_IMG = re.compile(
    r"https?://[^\s\"'\\]+?(?:brandboom|cloudfront|amazonaws)[^\s\"'\\]*?\.(?:jpe?g|png|webp)",
    re.IGNORECASE,
)


def fetch_brandboom(url: str, dest_dir: Path, limit: int = 400) -> list[Path]:
    """Best-effort scrape of a public Brandboom presentation page.

    Brandboom renders its catalogue client-side, so this only works for share
    links whose HTML still embeds the image URLs. When it doesn't, the error
    tells the user to use Brandboom's own "Download images" export.
    """
    try:
        with _session() as session:
            resp = session.get(url, timeout=DOWNLOAD_TIMEOUT, allow_redirects=True)
            resp.raise_for_status()
            html = resp.text

            urls: list[str] = []
            seen: set[str] = set()
            for found in _BRANDBOOM_IMG.findall(html):
                clean = found.replace("\\/", "/")
                if clean not in seen:
                    seen.add(clean)
                    urls.append(clean)
            if not urls:
                raise SourceFetchError(
                    "No images could be read from that Brandboom link — Brandboom "
                    "loads its catalogue after login. Open the presentation, use "
                    "Brandboom's 'Download Images' export, and upload that ZIP here."
                )

            saved: list[Path] = []
            for i, image_url in enumerate(urls[:limit], start=1):
                try:
                    img_resp = session.get(image_url, stream=True, timeout=DOWNLOAD_TIMEOUT)
                    if img_resp.status_code != 200:
                        continue
                    ext = os.path.splitext(urlparse(image_url).path)[1].lower() or ".jpg"
                    dest = dest_dir / f"brandboom_{i:04d}{ext}"
                    _stream_to_file(img_resp, dest)
                    saved.append(dest)
                except requests.RequestException:
                    continue
            if not saved:
                raise SourceFetchError("Brandboom listed images but none could be downloaded.")
            return saved
    except SourceFetchError:
        raise
    except requests.RequestException as e:
        raise SourceFetchError(f"Could not read the Brandboom link: {e}") from e


# ── Direct URL ───────────────────────────────────────────────────────────────

def fetch_direct(url: str, dest_dir: Path) -> list[Path]:
    """Download any plain URL (a ZIP or a single image on a CDN)."""
    try:
        with _session() as session:
            resp = session.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT, allow_redirects=True)
            resp.raise_for_status()
            name = _filename_from_response(resp, "download.bin")
            dest = dest_dir / name
            _stream_to_file(resp, dest)
    except requests.RequestException as e:
        raise SourceFetchError(f"Could not download {url}: {e}") from e
    return [dest]


# ── PDF catalogue → images ───────────────────────────────────────────────────

_PDF_RAW_MODES = {
    ("DeviceRGB", 8): "RGB",
    ("DeviceGray", 8): "L",
    ("DeviceCMYK", 8): "CMYK",
    ("CalRGB", 8): "RGB",
    ("CalGray", 8): "L",
}


def _resolve(obj):
    """Follow a pdfminer indirect reference to the object it points at."""
    return obj.resolve() if hasattr(obj, "resolve") else obj


def _colorspace_name(stream) -> str:
    cs = _resolve((stream.attrs or {}).get("ColorSpace"))
    if isinstance(cs, list) and cs:
        cs = cs[0]
    return str(getattr(cs, "name", cs) or "").strip("/'\" ")


def _open_pdf_image(stream):
    """Decode one PDF image XObject into a PIL image, or None.

    Most streams (JPEG, JPEG2000, PNG-ish) are self-describing and PIL opens
    them directly. Flate/LZW-compressed streams are bare samples with no
    header, so those are rebuilt from the stream's own Width/Height/ColorSpace.
    """
    from PIL import Image

    raw = stream.get_data()
    if not raw:
        return None
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
        return img
    except Exception:
        pass

    attrs = stream.attrs or {}
    try:
        width = int(_resolve(attrs.get("Width")))
        height = int(_resolve(attrs.get("Height")))
        bpc = int(_resolve(attrs.get("BitsPerComponent")) or 8)
    except (TypeError, ValueError):
        return None
    mode = _PDF_RAW_MODES.get((_colorspace_name(stream), bpc))
    if not mode or width <= 0 or height <= 0:
        return None
    expected = width * height * len(mode)
    if len(raw) < expected:
        return None
    try:
        return Image.frombytes(mode, (width, height), raw[:expected])
    except Exception:
        return None


def _pdf_stream_to_rgb(stream):
    """One PDF image as a flattened RGB image on a white background.

    PDFs keep transparency in a separate **SMask** stream, and the colour
    channels underneath a transparent area are usually black. Ignoring the
    SMask is what turned catalogue packshots into black rectangles — so the
    mask is decoded and used to composite the image onto white, which is what
    the page itself renders.
    """
    from PIL import Image

    base = _open_pdf_image(stream)
    if base is None:
        return None

    if base.mode == "CMYK":
        base = base.convert("RGB")
    elif base.mode == "P":
        base = base.convert("RGBA")

    # Transparency carried on the image itself (rare in PDFs, common in PNGs).
    alpha = None
    if base.mode in ("RGBA", "LA"):
        alpha = base.split()[-1]
        base = base.convert("RGB")
    elif base.mode != "RGB":
        base = base.convert("RGB")

    # The PDF's own soft mask wins — it is the real transparency.
    smask_ref = (stream.attrs or {}).get("SMask")
    if smask_ref is not None:
        try:
            smask = _open_pdf_image(_resolve(smask_ref))
            if smask is not None:
                alpha = smask.convert("L")
        except Exception:
            pass

    if alpha is None:
        return base

    if alpha.size != base.size:
        alpha = alpha.resize(base.size, Image.LANCZOS)
    flattened = Image.new("RGB", base.size, (255, 255, 255))
    flattened.paste(base, mask=alpha)
    return flattened


def extract_pdf_images(pdf_path: Path, dest_dir: Path, min_pixels: int = 40_000) -> list[Path]:
    """Pull the embedded photos out of a catalogue/lookbook PDF.

    Tiny images (logos, rules, icons) are skipped via ``min_pixels``.
    """
    try:
        import pdfplumber
    except ImportError as e:  # pragma: no cover - dependency is in requirements
        raise SourceFetchError("PDF support is unavailable on this server.") from e

    saved: list[Path] = []
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page_no, page in enumerate(pdf.pages, start=1):
                for img_no, image in enumerate(page.images or [], start=1):
                    stream = image.get("stream")
                    if stream is None:
                        continue
                    try:
                        img = _pdf_stream_to_rgb(stream)
                        if img is None or img.width * img.height < min_pixels:
                            continue
                        dest = dest_dir / f"{pdf_path.stem}_p{page_no:03d}_{img_no:02d}.jpg"
                        img.save(dest, format="JPEG", quality=92)
                        img.close()
                        saved.append(dest)
                    except Exception:
                        continue
    except Exception as e:
        raise SourceFetchError(f"Could not read images out of the PDF: {e}") from e

    if not saved:
        raise SourceFetchError(
            f"No product photos could be extracted from '{pdf_path.name}'. "
            "Vector-only catalogues have no embedded photos — export the images "
            "from the catalogue and upload them as a ZIP."
        )
    return saved


# Catalogue pages land in their own directory so the matcher can tell a page
# crop apart from the brand's own packshot and rank it accordingly.
CATALOG_DIR_NAME = "_catalog_pages"


def expand_pdfs(root: Path) -> int:
    """Replace every PDF under ``root`` with the photos extracted from it."""
    count = 0
    for pdf in list(root.rglob("*.pdf")):
        try:
            extracted = extract_pdf_images(pdf, root / CATALOG_DIR_NAME / pdf.stem)
            count += len(extracted)
            pdf.unlink(missing_ok=True)
        except SourceFetchError as e:
            logger.warning(f"PDF extraction skipped for {pdf.name}: {e}")
    return count


# ── Entry point ──────────────────────────────────────────────────────────────

def fetch_image_source(url: str, dest_dir: Path) -> dict:
    """Fetch one image-source link into ``dest_dir``.

    Returns ``{"kind", "files", "url"}``. ZIPs are left as-is — the collector
    unpacks them — but PDFs are expanded into photos here.
    """
    url = str(url or "").strip()
    if not url:
        raise SourceFetchError("No link given.")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    kind = detect_source(url)
    if kind == "dropbox":
        files = fetch_dropbox(url, dest_dir)
    elif kind == "gdrive":
        files = fetch_gdrive(url, dest_dir)
    elif kind == "brandboom":
        files = fetch_brandboom(url, dest_dir)
    elif kind == "gsheet":
        raise SourceFetchError(
            "That is a Google Sheets link — use it as the master file, not as an image source."
        )
    elif kind == "direct":
        files = fetch_direct(url, dest_dir)
    else:
        raise SourceFetchError(f"'{url}' is not a valid link.")

    expand_pdfs(dest_dir)
    return {"kind": kind, "url": url, "files": [f.name for f in files]}


def save_upload(data: bytes, filename: str, dest_dir: Path) -> Path:
    """Write an uploaded file into the run's source directory."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[/\\]+", "_", os.path.basename(filename or "upload"))
    dest = dest_dir / safe
    counter = 1
    while dest.exists():
        counter += 1
        dest = dest_dir / f"{Path(safe).stem}_{counter}{Path(safe).suffix}"
    with open(dest, "wb") as out:
        out.write(data)
    return dest


def cleanup_dir(path: str | Path) -> None:
    shutil.rmtree(Path(path), ignore_errors=True)
