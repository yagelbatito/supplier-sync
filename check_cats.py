import requests, os, warnings
warnings.filterwarnings("ignore")
from dotenv import load_dotenv
load_dotenv()
key = os.getenv("WOOCOMMERCE_KEY")
secret = os.getenv("WOOCOMMERCE_SECRET")
url = os.getenv("WOOCOMMERCE_URL")
s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
resp = s.get(
    url.rstrip("/") + "/wp-json/wc/v3/products/categories",
    params={"consumer_key": key, "consumer_secret": secret, "per_page": 100},
    verify=False
)
print("STATUS:", resp.status_code)
if resp.status_code == 200:
    cats = resp.json()
    print("סהכ:", len(cats))
    for c in sorted(cats, key=lambda x: x["name"]):
        print(str(c["id"]).ljust(6), c["name"])
else:
    print("ERROR:", resp.text[:200])
