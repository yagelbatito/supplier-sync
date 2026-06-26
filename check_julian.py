"""Diagnose Julian/Golyan scraper — fetch and inspect."""
import warnings
warnings.filterwarnings("ignore")

from src.scraping.http_client import HttpClient

BASE = "https://www.mydgolyan.com"
URLS = [
    # Original (from code)
    f"{BASE}/קטגוריות/%d7%a8%d7%99%d7%94%d7%95%d7%98-%d7%9e%d7%a9%d7%9c%d7%99%d7%9d/%d7%9b%d7%a1%d7%90%d7%95%d7%aa-%d7%9b%d7%95%d7%a8%d7%a1%d7%90%d7%95%d7%aa-%d7%95%d7%a1%d7%a4%d7%95%d7%aa/",
    # Plain Hebrew
    f"{BASE}/קטגוריות/ריהוט-משלים/כסאות-כורסאות-וספות/",
    # Parent category
    f"{BASE}/קטגוריות/ריהוט-משלים/",
]

http = HttpClient(verify_ssl=False, request_delay=0)
for u in URLS:
    print(f"\n=== {u}")
    try:
        resp = http.get(u)
        print(f"  status: {resp.status_code}")
        print(f"  size:   {len(resp.text)}")
        # Look for product listings
        text = resp.text
        # check for woocommerce product hooks
        for marker in ["woocommerce-loop-product", "product type-product", 'class="product', "li class=\"product", "products columns-"]:
            count = text.count(marker)
            if count:
                print(f"  marker {marker!r}: {count}")
        # find product hrefs
        import re
        # any href that doesn't look like a category
        prods = re.findall(r'<a [^>]*href="(https://www\.mydgolyan\.com/[^"]+)"[^>]*class="[^"]*woocommerce-LoopProduct-link', text)
        print(f"  woocommerce-LoopProduct-link hrefs: {len(prods)}")
        for p in prods[:3]:
            import urllib.parse
            print(f"    → {urllib.parse.unquote(p)}")
    except Exception as e:
        print(f"  EXCEPTION: {type(e).__name__}: {e}")
