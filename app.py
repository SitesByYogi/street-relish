from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, urlencode
from urllib.request import Request, urlopen
import csv, io, json, os, re, threading, time
from collections import defaultdict, Counter

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

ROOT = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(ROOT, "data", "sample-route.csv")
RUNTIME = os.path.join(ROOT, "data", "runtime")
GEOCACHE = os.path.join(RUNTIME, "geocode-cache.json")
HOST = os.environ.get("STREETRELISH_HOST", "127.0.0.1")
PORT = int(os.environ.get("STREETRELISH_PORT", "8787"))
_geo_lock = threading.Lock()
_last_geo = [0.0]

def clean(v):
    return (v or "").strip()

def num(v, default=0):
    try:
        return float(v)
    except Exception:
        return default

def addr_key(r):
    parts = [clean(r.get(k)).upper() for k in ("Address", "City", "State", "Zip")]
    return "|".join(re.sub(r"[^A-Z0-9]+", " ", p).strip() for p in parts)

def geo_key(address, city, state, zipcode):
    return addr_key({"Address":address,"City":city,"State":state,"Zip":zipcode})

def load_geocache():
    try:
        with open(GEOCACHE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_geocache(cache):
    if not os.path.isdir(RUNTIME):
        os.makedirs(RUNTIME)
    tmp = GEOCACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)
    os.replace(tmp, GEOCACHE)

def geocode(address, city, state, zipcode):
    key = geo_key(address, city, state, zipcode)
    with _geo_lock:
        cache = load_geocache()
        if key in cache:
            result = dict(cache[key])
            result["cached"] = True
            return result
        wait = 1.05 - (time.time() - _last_geo[0])
        if wait > 0:
            time.sleep(wait)
        query = ", ".join(x for x in [address, city, state, zipcode] if x)
        url = "https://nominatim.openstreetmap.org/search?" + urlencode({
            "format":"jsonv2","limit":1,"countrycodes":"us","q":query
        })
        req = Request(url, headers={
            "User-Agent":"StreetRelish/0.2 (https://sr.yogisvps.com)",
            "Accept":"application/json"
        })
        try:
            with urlopen(req, timeout=12) as response:
                data = json.loads(response.read().decode("utf-8"))
        finally:
            _last_geo[0] = time.time()
        result = {"lat":None,"lon":None,"display_name":"","cached":False}
        if data:
            result.update({"lat":float(data[0]["lat"]),"lon":float(data[0]["lon"]),"display_name":data[0].get("display_name","")})
        cache[key] = {"lat":result["lat"],"lon":result["lon"],"display_name":result["display_name"]}
        save_geocache(cache)
        return result

def parse_csv_bytes(raw):
    text = raw.decode("utf-8-sig", errors="replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    return normalize(rows)

def normalize(rows):
    source, deliveries, operational = [], [], []
    for i, r in enumerate(rows, 1):
        row = {k: clean(v) for k, v in r.items()}
        row["_source_row"] = i
        source.append(row)
        if row.get("StopTypeDesc", "").lower() == "del":
            deliveries.append(row)
        else:
            operational.append(row)
    groups = defaultdict(list)
    for r in deliveries:
        groups[addr_key(r)].append(r)
    stops = []
    cache = load_geocache()
    for pkgs in groups.values():
        first = pkgs[0]
        weight = sum(num(p.get("Weight")) for p in pkgs)
        pieces = sum(int(num(p.get("Pieces"))) for p in pkgs)
        comments = [p["DestComments"] for p in pkgs if p.get("DestComments")]
        cached = cache.get(geo_key(first.get("Address"),first.get("City"),first.get("State"),first.get("Zip")), {})
        stops.append({
            "id": len(stops)+1,"name":first.get("Name"),"address":first.get("Address"),"city":first.get("City"),"state":first.get("State"),"zip":first.get("Zip"),
            "weight":round(weight,1),"pieces":pieces,"package_count":len(pkgs),"heavy":weight>=20,"comments":list(dict.fromkeys(comments)),
            "lat":cached.get("lat"),"lon":cached.get("lon"),
            "packages":[{"order_id":p.get("OrderID"),"reference":p.get("Reference1"),"barcode":p.get("Barcode2"),"weight":num(p.get("Weight")),"pieces":int(num(p.get("Pieces"))),"deadline":p.get("DelvTime")} for p in pkgs]
        })
    stops.sort(key=lambda s:(s["zip"],re.sub(r"^\d+\s*","",s["address"].upper()),s["address"]))
    for i,s in enumerate(stops,1): s["plan_sequence"]=i
    zips=Counter(r.get("Zip") for r in deliveries); weights=[num(r.get("Weight")) for r in deliveries]
    route=next((r.get("RouteID") for r in rows if r.get("RouteID")),""); driver=next((r.get("DriverID") for r in rows if r.get("DriverID")),""); date=next((r.get("PostDate") for r in rows if r.get("PostDate")),"")
    summary={"route":route,"driver":driver,"date":date,"source_rows":len(rows),"delivery_records":len(deliveries),"stops":len(stops),"zip_count":len(zips),"zips":dict(sorted(zips.items())),"heavy_packages":sum(1 for w in weights if w>=20),"total_weight":round(sum(weights),1),"operational_rows":len(operational),"sequence_zero":sum(1 for r in rows if clean(r.get("Sequence")) in ("0","0.0")),"cached_coordinates":sum(1 for s in stops if s.get("lat") is not None)}
    return {"summary":summary,"stops":stops,"load_plan":list(reversed(stops)),"source_rows":source}

class Handler(SimpleHTTPRequestHandler):
    def translate_path(self,path):
        rel=urlparse(path).path.lstrip("/") or "index.html"
        return os.path.join(ROOT,"static",rel)
    def do_GET(self):
        path=urlparse(self.path).path
        if path=="/healthz": return self.json({"ok":True,"service":"streetrelish"})
        if path=="/api/sample":
            with open(SAMPLE,"rb") as f: return self.json(parse_csv_bytes(f.read()))
        return super().do_GET()
    def do_POST(self):
        path=urlparse(self.path).path
        if path=="/api/geocode":
            try:
                length=int(self.headers.get("Content-Length","0")); payload=json.loads(self.rfile.read(length).decode("utf-8"))
                return self.json(geocode(clean(payload.get("address")),clean(payload.get("city")),clean(payload.get("state")),clean(payload.get("zip"))))
            except Exception as exc: return self.json({"error":str(exc)},500)
        if path!="/api/import": return self.send_error(404)
        ctype=self.headers.get("Content-Type","")
        if "multipart/form-data" not in ctype: return self.send_error(400,"Expected multipart form")
        boundary=ctype.split("boundary=")[-1].encode(); body=self.rfile.read(int(self.headers.get("Content-Length","0"))); raw=None
        for part in body.split(b"--"+boundary):
            if b'name="file"' in part:
                raw=part.split(b"\r\n\r\n",1)[1].rsplit(b"\r\n",1)[0]; break
        if raw is None: return self.send_error(400,"No file")
        try: return self.json(parse_csv_bytes(raw))
        except Exception as exc: return self.json({"error":str(exc)},500)
    def json(self,obj,status=200):
        payload=json.dumps(obj).encode(); self.send_response(status); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",len(payload)); self.end_headers(); self.wfile.write(payload)

if __name__=="__main__":
    print("StreetRelish V1 running at http://{}:{}".format(HOST,PORT))
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()
