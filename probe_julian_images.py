"""Render a Julian category page in Chrome headless, capture all image URLs."""
import warnings, time
warnings.filterwarnings("ignore")

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--disable-gpu")
opts.add_argument("--no-sandbox")
opts.add_argument("--ignore-certificate-errors")
opts.add_argument("--window-size=1280,1024")

driver = webdriver.Chrome(options=opts)
try:
    url = "https://www.mydgolyan.com/קטגוריות/ריהוט-משלים/מראות/"
    print(f"Navigating to: {url}")
    driver.get(url)
    # Wait for WizShop JS to render
    time.sleep(8)

    # Collect every <img> src
    imgs = driver.find_elements(By.TAG_NAME, "img")
    print(f"\nFound {len(imgs)} <img> elements")
    seen = set()
    for el in imgs:
        src = el.get_attribute("src") or ""
        if not src or src in seen:
            continue
        seen.add(src)
        if "wizsoft" in src or "shop" in src.lower() or any(d in src for d in ["jpg", "jpeg", "png", "webp"]):
            print(f"  IMG: {src}")

    # Also pull performance entries (every network request)
    print()
    print("=== Performance/network entries ===")
    entries = driver.execute_script("return performance.getEntriesByType('resource').map(e => ({name: e.name, type: e.initiatorType}))")
    for e in entries:
        n = e.get("name", "")
        if any(ext in n.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"]):
            print(f"  {e['type']:>10}  {n}")
finally:
    driver.quit()
