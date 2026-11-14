# -*- coding: utf-8 -*-
"""Clean M.M.S product images: remove the supplier's promo overlays.

Two overlays appear on M.M.S source images:
  1. a round black "חיסול מלאי" clearance BADGE in the top-right (with yellow
     text + a wholesale price), and
  2. a dark INFO BANNER across the bottom (name / model / dimensions + thumbnails).

`clean()` removes both: the badge is detected (near-black circle in the top-right
that contains saturated-yellow text) and inpainted away; the bottom banner (a dark,
low-saturation band spanning the width) is cropped off. It also OCRs the price
printed inside the badge (via GPT vision) — that is the wholesale/clearance price,
which the owner doubles for the store price.
"""
import base64
import json

import cv2
import numpy as np


def detect_circle(img):
    """(cx, cy, r) of the clearance badge, or None."""
    H, W = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    reg = np.zeros((H, W), np.uint8)
    reg[0:int(0.62 * H), int(0.45 * W):W] = 255
    dark = ((gray < 80).astype(np.uint8) * 255) & reg
    dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(dark, 8)
    yt = cv2.inRange(hsv, (22, 150, 160), (34, 255, 255))
    for i in sorted(range(1, n), key=lambda k: -stats[k, cv2.CC_STAT_AREA]):
        if stats[i, cv2.CC_STAT_AREA] < 0.012 * W * H:
            break
        bw, bh = stats[i, 2], stats[i, 3]
        if not (0.8 <= bw / float(bh) <= 1.25):
            continue
        comp = (lab == i).astype(np.uint8)
        cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        f = np.zeros_like(comp)
        cv2.drawContours(f, cnts, -1, 1, cv2.FILLED)
        pts = np.column_stack(np.where(f > 0))[:, ::-1].astype(np.int32)
        (cx, cy), r = cv2.minEnclosingCircle(pts)
        if f.sum() / (np.pi * r * r + 1e-6) < 0.72:
            continue
        x0, y0 = stats[i, 0], stats[i, 1]
        if int(yt[y0:y0 + bh, x0:x0 + bw].sum() / 255) < 600:
            continue
        return (int(cx), int(cy), int(r))
    return None


def detect_banner(img):
    """Top y of the bottom info banner (dark, greyish, full-width), or None."""
    H, W = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    dark = (gray < 95) & (hsv[:, :, 1] < 60)
    frac = dark.mean(axis=1)
    top = None
    for y in range(H - 1, int(0.5 * H), -1):
        if frac[y] > 0.6:
            top = y
        elif top is not None:
            break
    if top is None or (H - top) < 0.08 * H:
        return None
    return top


def remove_circle(img, circle):
    cx, cy, r = circle
    mask = np.zeros(img.shape[:2], np.uint8)
    cv2.circle(mask, (cx, cy), r + 12, 255, -1)
    return cv2.inpaint(img, mask, 7, cv2.INPAINT_TELEA)


def read_circle_price(img, circle, ai):
    """Price (int ₪) printed inside the badge via GPT vision, or None."""
    if ai is None or not getattr(ai, "available", False):
        return None
    cx, cy, r = circle
    crop = img[max(0, cy - r):cy + r, max(0, cx - r):cx + r]
    ok, buf = cv2.imencode(".jpg", crop)
    if not ok:
        return None
    uri = "data:image/jpeg;base64," + base64.b64encode(buf).decode()
    msg = [{"role": "user", "content": [
        {"type": "text", "text": "בתמונה עיגול פרסום של ספק. קרא את הטקסט. אם מופיע "
         "מחיר בשקלים (₪) החזר את המספר. החזר JSON בלבד: "
         "{\"price\": <מספר שלם או null>}"},
        {"type": "image_url", "image_url": {"url": uri}}]}]
    raw = ai.chat_messages(msg, max_tokens=120, temperature=0, json_mode=True)
    try:
        v = json.loads(raw).get("price")
        return int(v) if v not in (None, "", 0) else None
    except Exception:
        return None


def clean(img, ai=None):
    """Remove badge + banner. Returns (clean_img, info) where info has keys
    circle, banner (bool/coords) and circle_price (int or None)."""
    info = {"circle": None, "banner": None, "circle_price": None}
    out = img
    c = detect_circle(out)
    if c:
        info["circle"] = c
        info["circle_price"] = read_circle_price(out, c, ai)
        out = remove_circle(out, c)
    b = detect_banner(out)
    if b is not None:
        info["banner"] = b
        out = out[:b]
    return out, info
