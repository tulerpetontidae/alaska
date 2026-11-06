#!/usr/bin/env python3
"""Build data.json for the Alaska winter page.

  python3 build.py

The output is one self-contained index.html: template.html with the data,
the ski tracks and Leaflet (vendor/) inlined. Only map tiles and web fonts
load from the network.

base.json holds the trip, the candidate houses, the plan and the notes.
sources/*.json are the researched places, one list per topic. Drive times from
downtown and from each house come from OSRM's public table service and are
cached in drive.json, so a rebuild only asks for places it hasn't seen.
"""
import json, glob, os, math, datetime as dt, urllib.request

DOWNTOWN = {"id": "downtown", "lat": 61.2181, "lon": -149.9003}

# ---------- ski tracks from OpenStreetMap (piste:type=nordic) ----------
def enc(pts):
    out, plat, plon = [], 0, 0
    for la, lo in pts:
        for v, prev in ((round(la * 1e5), plat), (round(lo * 1e5), plon)):
            d = v - prev; d = ~(d << 1) if d < 0 else d << 1
            while d >= 0x20: out.append(chr((0x20 | (d & 0x1f)) + 63)); d >>= 5
            out.append(chr(d + 63))
        plat, plon = round(la * 1e5), round(lo * 1e5)
    return "".join(out)

def simplify(pts, tol_m=4.0):
    if len(pts) < 3: return pts
    k = math.cos(math.radians(pts[0][0])) * 111320
    xy = [(lo * k, la * 110540) for la, lo in pts]
    keep = [False] * len(pts); keep[0] = keep[-1] = True; st = [(0, len(pts) - 1)]
    while st:
        a, b = st.pop(); (x1, y1), (x2, y2) = xy[a], xy[b]; L = math.hypot(x2 - x1, y2 - y1) or 1e-9; best, bi = 0, None
        for i in range(a + 1, b):
            x, y = xy[i]; dd = abs((x2 - x1) * (y1 - y) - (x1 - x) * (y2 - y1)) / L
            if dd > best: best, bi = dd, i
        if bi is not None and best > tol_m: keep[bi] = True; st += [(a, bi), (bi, b)]
    return [p for p, kp in zip(pts, keep) if kp]

def length_km(pts):
    r = math.radians
    return sum(6371 * 2 * math.asin(math.sqrt(math.sin(r(b[0] - a[0]) / 2) ** 2 + math.cos(r(a[0])) * math.cos(r(b[0])) * math.sin(r(b[1] - a[1]) / 2) ** 2))
               for a, b in zip(pts, pts[1:]))

def style_of(t):
    g = (t.get("piste:grooming") or "").replace("+", ";")
    parts = set(x.strip() for x in g.split(";") if x.strip())
    if "backcountry" in parts: return "bc"
    c, s = "classic" in parts, "skating" in parts
    return "both" if c and s else "classic" if c else "skate" if s else "?"

def build_tracks(skiplaces):
    if not os.path.exists("sources/osm-nordic.raw"): return None, None
    raw = json.load(open("sources/osm-nordic.raw"))
    out, sys_stats = [], {}
    heads = [p for p in skiplaces if p.get("lat") is not None]
    for e in raw["elements"]:
        if e["type"] != "way" or not e.get("geometry"): continue
        t = e.get("tags", {}); pts = [(g["lat"], g["lon"]) for g in e["geometry"]]
        st = style_of(t); lit = t.get("lit") == "yes"; km_ = length_km(pts)
        mid = pts[len(pts) // 2]
        near = min(heads, key=lambda p: (p["lat"] - mid[0]) ** 2 + ((p["lon"] - mid[1]) * 0.48) ** 2) if heads else None
        if near and length_km([mid, (near["lat"], near["lon"])]) > 8: near = None
        sid = near["id"] if near else None
        out.append({"p": enc(simplify(pts)), "s": st, "l": 1 if lit else 0, "n": t.get("piste:name") or t.get("name") or "",
                    "k": round(km_, 2), "sys": sid})
        if sid:
            ss = sys_stats.setdefault(sid, {"km": 0, "lit": 0, "classic": 0, "skate": 0})
            ss["km"] += km_; ss["lit"] += km_ if lit else 0
            ss["classic"] += km_ if st in ("both", "classic") else 0
            ss["skate"] += km_ if st in ("both", "skate") else 0
    for v in sys_stats.values():
        for k in v: v[k] = round(v[k], 1)
    tracks = {"source": "OpenStreetMap contributors, piste:type=nordic", "fetched": raw.get("osm3s", {}).get("timestamp_osm_base", ""), "ways": out}
    return sys_stats, tracks

def main():
    base = json.load(open("base.json"))
    places, seen = [], set()
    for f in sorted(glob.glob("sources/*.json")):
        for p in json.load(open(f)):
            if p["id"] in seen: raise SystemExit("duplicate id %s in %s" % (p["id"], f))
            seen.add(p["id"]); places.append(p)
    drive = json.load(open("drive.json")) if os.path.exists("drive.json") else {}
    origins = [DOWNTOWN] + base["stays"]
    todo = [p for p in places if p.get("lat") is not None and any(p["id"] not in drive.get(o["id"], {}) for o in origins)]
    for i in range(0, len(todo), 80):
        chunk = todo[i:i + 80]
        pts = origins + chunk
        coords = ";".join("%.5f,%.5f" % (p["lon"], p["lat"]) for p in pts)
        url = ("https://router.project-osrm.org/table/v1/driving/%s?sources=%s&destinations=%s&annotations=duration"
               % (coords, ";".join(map(str, range(len(origins)))), ";".join(map(str, range(len(origins), len(pts))))))
        r = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "trip-planner/1.0"}), timeout=60))
        for oi, o in enumerate(origins):
            for pi, p in enumerate(chunk):
                s = r["durations"][oi][pi]
                drive.setdefault(o["id"], {})[p["id"]] = None if s is None else round(s / 60)
    json.dump(drive, open("drive.json", "w"), indent=0, sort_keys=True)
    stats, tracks = build_tracks([p for p in places if p["cat"] == "ski"])
    if stats:
        for p in places:
            if p["id"] in stats: p["osm"] = stats[p["id"]]
    data = dict(base, places=places, drive=drive, generated=dt.date.today().isoformat())
    def js(o):  # JSON safe inside a <script> element
        return json.dumps(o, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = open("template.html").read()
    for k, v in (("/*LEAFLET_CSS*/", open("vendor/leaflet.css").read()), ("/*LEAFLET_JS*/", open("vendor/leaflet.js").read()),
                 ("/*DATA*/", js(data)), ("/*TRACKS*/", js(tracks) if tracks else "")):
        assert html.count(k) == 1, k
        html = html.replace(k, v)
    open("index.html", "w").write(html)
    print("index.html %.0f KB" % (len(html.encode()) / 1024))
    by = {}
    for p in places: by[p["cat"]] = by.get(p["cat"], 0) + 1
    print(len(places), "places:", ", ".join("%s %d" % kv for kv in sorted(by.items())))

if __name__ == "__main__":
    main()
