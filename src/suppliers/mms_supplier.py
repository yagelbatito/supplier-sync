# -*- coding: utf-8 -*-
"""
M.M.S supplier — a Dropbox folder of product images.

M.M.S has no data feed: each product is an IMAGE with a dark banner at the
bottom carrying the name, "מק״ט: <sku>/<price>" and dimensions. This module
downloads the shared Dropbox folder (as a zip), and for every image:
  * OCRs the banner (OpenAI vision) → name, sku (before the /), price (after
    the /), dimensions,
  * crops the banner off → a clean product image (bytes),
returning normalized product dicts. Pricing/classification/upload happen in
mms_sync.py. Supplier name "M.M.S" is stored only in meta (next to the SKU),
never shown to customers.
"""
import base64
import io
import json
import re
import zipfile

import numpy as np
import requests
from PIL import Image

from src.core.logger import get_logger

logger = get_logger("supplier.mms")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

_OCR_PROMPT = ('בתמונה יש באנר כהה בתחתית. תעתק ממנו בדיוק: שם המוצר, את המחרוזת המלאה '
               'שמופיעה אחרי "מק״ט:" (בפורמט מספר/מספר), ואת המידות. החזר JSON תקין בלבד: '
               '{"name":"","mkt":"","dimensions":""}')


def _dl_url(share_url: str) -> str:
    """Turn a Dropbox share link into a direct zip download."""
    u = share_url.strip()
    u = re.sub(r"[?&]dl=\d", "", u)
    sep = "&" if "?" in u else "?"
    return f"{u}{sep}dl=1"


def download_zip(share_url: str) -> zipfile.ZipFile:
    r = requests.get(_dl_url(share_url), headers={"User-Agent": UA}, timeout=600, verify=False)
    r.raise_for_status()
    return zipfile.ZipFile(io.BytesIO(r.content))


def crop_banner(im: Image.Image):
    """Remove the dark bottom banner. Picks the bottom-most dark horizontal band
    (robust to dark products / very tall images)."""
    W, H = im.size
    rows = np.asarray(im.convert("L")).mean(axis=1)
    dark = rows < 125
    runs, i = [], 0
    while i < H:
        if dark[i]:
            j = i
            while j < H and dark[j]:
                j += 1
            runs.append((i, j))
            i = j
        else:
            i += 1
    cand = [r for r in runs if r[1] >= H * 0.6]     # band sitting in the lower part
    if not cand:
        return im
    top, _ = max(cand, key=lambda r: r[1])          # bottom-most band = banner
    if (H - top) > H * 0.42:                         # sanity: banner ≈ bottom strip
        top = int(H * 0.72)
    return im.crop((0, 0, W, top))


def _ocr(cli, img_bytes: bytes, model: str) -> dict:
    b64 = base64.b64encode(img_bytes).decode()
    r = cli.chat.completions.create(model=model, temperature=0, max_tokens=200,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": _OCR_PROMPT},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}}]}])
    txt = r.choices[0].message.content.strip().strip("`")
    txt = re.sub(r"^json", "", txt).strip()
    try:
        return json.loads(txt)
    except Exception:
        return {}


def _sku_from_name(fn: str) -> str:
    m = re.findall(r"\d{5,8}", fn.split("/")[-1])
    return m[-1] if m else ""


def fetch_all(share_url: str, ai_client, ocr_model: str = "gpt-4o-mini", max_items: int = 0) -> list:
    """Download the Dropbox folder and OCR+crop every product image."""
    zf = download_zip(share_url)
    imgs = [n for n in zf.namelist() if n.lower().endswith((".jpg", ".jpeg", ".png"))]
    logger.info(f"M.M.S: {len(imgs)} images in the Dropbox folder")
    out = []
    for idx, name in enumerate(imgs):
        if max_items and len(out) >= max_items:
            break
        try:
            raw = zf.open(name).read()
            im = Image.open(io.BytesIO(raw)).convert("RGB")
            d = _ocr(ai_client, raw, ocr_model)
            mkt = str(d.get("mkt", ""))
            sku = mkt.split("/")[0].strip() if "/" in mkt else _sku_from_name(name)
            sku = re.sub(r"\D", "", sku) or _sku_from_name(name)
            price = 0.0
            if "/" in mkt:
                price = float(re.sub(r"\D", "", mkt.split("/")[1]) or 0)
            pname = (d.get("name") or "").strip()
            if not sku or not pname:
                logger.warning(f"M.M.S: skip (no sku/name): {name}")
                continue
            clean = crop_banner(im)
            buf = io.BytesIO()
            clean.save(buf, format="JPEG", quality=90)
            out.append({
                "sku": sku,
                "name": pname,
                "price": price,                       # banner price; ×1.7 downstream
                "dimensions": (d.get("dimensions") or "").strip(),
                "source_cat": name.split("/")[0],      # M.M.S folder (hint only)
                "image_bytes": buf.getvalue(),
                "image_filename": f"mms-{sku}.jpg",
            })
            if (idx + 1) % 25 == 0:
                logger.info(f"M.M.S: OCR+crop {idx + 1}/{len(imgs)}")
        except Exception as exc:
            logger.warning(f"M.M.S: failed on {name}: {exc}")
    logger.info(f"M.M.S fetch finished: {len(out)} products")
    return out
