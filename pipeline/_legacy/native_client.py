"""Native Kinetic protocol client (Branch A). Reproduces the application's own DETERMINISTIC
request headers — proven, not forged:
  x-resource-id = SHA256_hex(bearer token)                 [PROVEN byte-for-byte vs native capture]
  x-meta-info   = SHA256_hex(device-attr string)           [algorithm = app c(); field list source-confirmed]
  device-id     = session-store UUID (per worker)          [app state]
  session-id    = JWT sessionId                            [app state]
Custom headers apply ONLY to buy.gokinetic.com/api calls (shouldIncludeCustomHeaders predicate).
No Cloudflare/HUMAN/behavioral tokens are ever produced, replayed, or imported.

One worker owns one session, one cookie jar, one token, one US route through the whole
homepage -> auth -> search -> product lifecycle. 401 reauthenticates on the same session/route.
403/429/timeout/malformed are NEVER address decisions.
"""
import os, json, base64, hashlib, uuid, time

BASE = "https://buy.gokinetic.com"
SESSION = "/api/v1/auth/session?context=residential"
SEARCH = "/api/v1/address/search"
PRODUCT = "/api/v2/windstream/product-availability"
API_PREDICATE = "https://buy.gokinetic.com/api"   # exact substring the app checks

# curl_cffi impersonation UA (must match the fingerprint we hash so the values are self-consistent)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def x_resource_id(token: str) -> str:
    # = u(getCartToken()); the token we hold. Empty pre-auth.
    return sha256_hex(token or "")

def fingerprint_string(fp: dict) -> str:
    # EXACT field order from _app c(): navigator.* + screen.* + timezoneOffset, joined by "|"
    return "|".join([
        str(fp["cookieEnabled"]).lower(), fp["userAgent"], fp["language"],
        str(fp["hardwareConcurrency"]), str(fp["maxTouchPoints"]),
        str(fp["webdriver"]).lower(), str(fp["pdfViewerEnabled"]).lower(),
        str(fp["screenWidth"]), str(fp["screenHeight"]), str(fp["availWidth"]),
        str(fp["availHeight"]), str(fp["colorDepth"]), str(fp["pixelDepth"]),
        str(fp["timezoneOffset"]),
    ])

def x_meta_info(fp: dict) -> str:
    return sha256_hex(fingerprint_string(fp))

# a self-consistent client fingerprint (matches our Chrome UA; deterministic per worker)
DEFAULT_FP = {
    "cookieEnabled": True, "userAgent": UA, "language": "en-US",
    "hardwareConcurrency": 8, "maxTouchPoints": 0, "webdriver": False,
    "pdfViewerEnabled": True, "screenWidth": 1920, "screenHeight": 1080,
    "availWidth": 1920, "availHeight": 1032, "colorDepth": 24, "pixelDepth": 24,
    "timezoneOffset": 300,
}

def jwt_session_id(token: str) -> str:
    try:
        pad = token.split(".")[1]; pad += "=" * (-len(pad) % 4)
        return json.loads(base64.urlsafe_b64decode(pad)).get("sessionId", "")
    except Exception:
        return ""

def custom_headers(token: str, device_id: str, fp: dict) -> dict:
    """The app's deterministic custom-header set, for a given token/session state."""
    return {
        "device-id": device_id,
        "session-id": jwt_session_id(token),
        "x-meta-info": x_meta_info(fp),
        "x-resource-id": x_resource_id(token),
    }

def wants_custom(url: str) -> bool:
    return API_PREDICATE in url.lower()

BASE_HEADERS = {
    "Origin": BASE, "Referer": BASE + "/", "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty", "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": UA,
}

def search_body(addr: dict) -> dict:
    """Native current body schema (postalCode, addressLine2, geoLat/geoLong, useSearchV2)."""
    return {
        "addressLine1": addr["line1"], "addressLine2": addr.get("line2", ""),
        "city": addr.get("city", ""), "state": addr["state"],
        "postalCode": str(addr.get("zip", ""))[:5],
        "geoLat": str(addr.get("lat", "") or ""), "geoLong": str(addr.get("lon", "") or ""),
        "useSearchV2": False,
    }


class NativeSession:
    """One persistent worker session/route. Requires a curl_cffi Session factory bound to a
    fixed US residential exit. Does homepage->auth->search->product with deterministic headers."""
    def __init__(self, make_session, basic_auth, fp=None):
        self.make_session = make_session          # () -> curl_cffi Session on a FIXED route
        self.basic = basic_auth
        self.fp = fp or DEFAULT_FP
        self.device_id = str(uuid.uuid4())
        self.s = None
        self.token = None

    def _open(self):
        self.s = self.make_session()
        self.s.get(BASE + "/", timeout=25)        # homepage bootstrap (NOT /api -> no custom headers)

    def auth(self):
        if self.s is None:
            self._open()
        ch = custom_headers(self.token or "", self.device_id, self.fp)   # pre-auth token=""
        r = self.s.post(BASE + SESSION, headers={**BASE_HEADERS, "Authorization": "Basic " + self.basic, **ch},
                        data="{}", timeout=30)
        if r.status_code in (200, 201):
            try:
                self.token = r.json().get("token")
            except Exception:
                self.token = None
        return r

    def _api(self, path, payload, retry_on_401=True):
        ch = custom_headers(self.token or "", self.device_id, self.fp)
        r = self.s.post(BASE + path, headers={**BASE_HEADERS, "Authorization": "Bearer " + (self.token or ""), **ch},
                        data=json.dumps(payload), timeout=30)
        if r.status_code == 401 and retry_on_401:
            self.auth()                            # reauth on SAME session/route, once
            return self._api(path, payload, retry_on_401=False)
        return r

    def search(self, addr):
        return self._api(SEARCH, search_body(addr))

    def product(self, df_address_id, uqual):
        return self._api(PRODUCT, {"dfAddressId": df_address_id, "accountNumber": "", "moveDate": "",
                                   "addAddrReason": "", "uqualProvisioningResult": uqual})
