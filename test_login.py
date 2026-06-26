import warnings, os
warnings.filterwarnings("ignore")
from dotenv import load_dotenv
load_dotenv()
import requests

LOGIN_URL = "https://b2b.paldinox.co.il/AmPortal/api/Auth/login"
resp = requests.post(LOGIN_URL, json={
    "userName": os.getenv("PALDINOX_USER"),
    "password": os.getenv("PALDINOX_PASSWORD"),
    "forceLogin": False,
    "deviceAppVer": "1.1.35",
    "deviceInfo": '{"os":{"name":"Android"}}',
    "language": "he"
}, headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}, verify=False)
print("Status:", resp.status_code)
print("Cookies:", dict(resp.cookies))
print("Body:", resp.text[:500])
