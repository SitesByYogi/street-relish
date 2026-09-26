from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse
import csv, io, json, os, re
from collections import defaultdict, Counter

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


ROOT = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(ROOT, "data", "sample-route.csv")
HOST = os.environ.get("STREETRELISH_HOST", "127.0.0.1")
PORT = int(os.environ.get("STREETRELISH_PORT", "8787"))


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
    for pkgs in groups.values():
        first = pkgs[0]
        weight = sum(num(p.get("Weight")) for p in pkgs)
        pieces = sum(int(num(p.get("Pieces"))) for p in pkgs)
        comments = [p["DestComments"] for p in pkgs if p.get("DestComments")]
        stops.append(
            {
                "id": len(stops) + 1,
                "name": first.get("Name"),
                "address": first.get("Address"),
                "city": first.get("City"),
                "state": first.get("State"),
                "zip": first.get("Zip"),
                "weight": round(weight, 1),
                "pieces": pieces,
                "package_count": len(pkgs),
                "heavy": weight >= 20,
                "comments": list(dict.fromkeys(comments)),
                "packages": [
                    {
                        "order_id": p.get("OrderID"),
                        "reference": p.get("Reference1"),
                        "barcode": p.get("Barcode2"),
                        "weight": num(p.get("Weight")),
                        "pieces": int(num(p.get("Pieces"))),
                        "deadline": p.get("DelvTime"),
                    }
                    for p in pkgs
                ],
            }
        )

    # Deterministic V1 planning baseline. This is an organization sequence,
    # not geographic route optimization.
    stops.sort(
        key=lambda s: (
            s["zip"],
            re.sub(r"^\d+\s*", "", s["address"].upper()),
            s["address"],
        )
    )
    for i, s in enumerate(stops, 1):
        s["plan_sequence"] = i

    zips = Counter(r.get("Zip") for r in deliveries)
    weights = [num(r.get("Weight")) for r in deliveries]
    route = next((r.get("RouteID") for r in rows if r.get("RouteID")), "")
    driver = next((r.get("DriverID") for r in rows if r.get("DriverID")), "")
    date = next((r.get("PostDate") for r in rows if r.get("PostDate")), "")

    summary = {
        "route": route,
        "driver": driver,
        "date": date,
        "source_rows": len(rows),
        "delivery_records": len(deliveries),
        "stops": len(stops),
        "zip_count": len(zips),
        "zips": dict(sorted(zips.items())),
        "heavy_packages": sum(1 for w in weights if w >= 20),
        "total_weight": round(sum(weights), 1),
        "operational_rows": len(operational),
        "sequence_zero": sum(
            1 for r in rows if clean(r.get("Sequence")) in ("0", "0.0")
        ),
    }

    # Load plan defaults to reverse delivery order. Heavy stops are highlighted
    # but are never silently reordered without driver input.
    return {
        "summary": summary,
        "stops": stops,
        "load_plan": list(reversed(stops)),
        "source_rows": source,
    }


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        rel = urlparse(path).path.lstrip("/") or "index.html"
        return os.path.join(ROOT, "static", rel)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/healthz":
            return self.json({"ok": True, "service": "streetrelish"})
        if path == "/api/sample":
            with open(SAMPLE, "rb") as f:
                return self.json(parse_csv_bytes(f.read()))
        return super().do_GET()

    def do_POST(self):
        if urlparse(self.path).path != "/api/import":
            return self.send_error(404)
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            return self.send_error(400, "Expected multipart form")

        boundary = ctype.split("boundary=")[-1].encode()
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        raw = None
        for part in body.split(b"--" + boundary):
            if b'name="file"' in part:
                raw = part.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0]
                break
        if raw is None:
            return self.send_error(400, "No file")
        try:
            return self.json(parse_csv_bytes(raw))
        except Exception as exc:
            return self.json({"error": str(exc)}, 500)

    def json(self, obj, status=200):
        payload = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(payload))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    print(f"StreetRelish V1 running at http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
