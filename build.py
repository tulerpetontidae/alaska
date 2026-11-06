#!/usr/bin/env python3
"""Build index.html for the Alaska New Year page.

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

# official groomed trails (Municipality of Anchorage NordicTrails layer) -> our ski places
MOA_SYS = {"Kincaid Ski Trails": "ski-kincaid", "Beach Lake Ski Trails": "ski-beach-lake", "Hillside Ski Trails": "ski-hillside",
           "Russian Jack Springs Park": "ski-russian-jack", "Bartlett Ski Trails": "ski-bartlett", "APU Trails": "ski-apu-goose",
           "Girdwood Multi-Use": "ski-girdwood-moose-meadows", "Girdwood Ski Trails": "ski-girdwood-5k",
           "Chester Creek Trail": "ski-chester-creek", "Mirror Lake MS/ Coyote": "ski-mirror-lake-coyote",
           "Campbell Creek Trail": "ski-campbell-creek-trail", "Chugach Foothills Connector": "ski-fnbp-tour",
           "Eagle River HS Ski Trails": "ski-erhs", "Yosemite Drive Trails": "ski-yosemite"}
MOA_SEG = {("Tour of Anchorage", "Coastal Trail"): "ski-coastal-trail", ("Tour of Anchorage", "Chester Creek Trail"): "ski-chester-creek",
           ("Tour of Anchorage", "Tour of Anchorage Trail"): "ski-fnbp-tour"}
BC_SYS = {"Powerline Pass": "ski-powerline-pass", "Eklutna Lakeside Trail": "ski-eklutna", "Turnagain Pass": "ski-turnagain-pass",
          "Johnson Pass": "ski-johnson-pass", "Resurrection Pass (north)": "ski-resurrection-north", "Indian Valley": "ski-indian-valley",
          "Rabbit Lake": "ski-rabbit-lake", "Middle Fork (Prospect Heights)": "ski-prospect-heights", "Archangel Road (Hatcher Pass)": "ski-archangel",
          "Matanuska Lakes": "ski-matanuska-lakes", "Nancy Lake: Lynx Lake route": "ski-nancy-lake", "Eagle River Nature Center": "ski-ernc"}

def densify(pts, step_m=20):
    out = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        n = max(1, int(length_km([a, b]) * 1000 / step_m))
        out += [(a[0] + (b[0] - a[0]) * t / n, a[1] + (b[1] - a[1]) * t / n) for t in range(1, n + 1)]
    return out

def build_tracks(skiplaces):
    out, stats = [], {}
    def add(rec, pts):
        rec["p"] = enc(simplify(pts)); rec["k"] = round(length_km(pts), 2); out.append(rec)
        sid = rec.get("sys")
        if sid:
            v = stats.setdefault(sid, {"km": 0, "lit": 0, "classic": 0, "skate": 0, "skiOnly": 0, "src": rec["src"]})
            v["km"] += rec["k"]; v["lit"] += rec["k"] if rec.get("l") else 0
            v["classic"] += rec["k"] if rec["s"] in ("both", "classic") else 0
            v["skate"] += rec["k"] if rec["s"] in ("both", "skate") else 0
            v["skiOnly"] += rec["k"] if rec.get("so") else 0
    # 1. official groomed network
    grid = {}
    if os.path.exists("sources/moa-nordic.geojson.raw"):
        for f in json.load(open("sources/moa-nordic.geojson.raw"))["features"]:
            pr, g = f["properties"], f["geometry"]
            lines = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
            for ln in lines:
                pts = [(c[1], c[0]) for c in ln]
                if len(pts) < 2: continue
                for la, lo in densify(pts): grid.setdefault((round(la * 2500), round(lo * 1200)), []).append((la, lo))
                sysn, seg = pr["Grooming_System"], pr["Grooming_Segment"] or ""
                add({"src": "moa", "oid": pr["OBJECTID"], "g": sysn, "n": seg,
                     "s": "both" if pr["Track_Set"] == "Yes" else "skate", "so": 1 if pr["Ski_Only"] == "Yes" else 0,
                     "l": 1 if pr["Lighted_Trail"] == "Yes" else 0, "lvl": pr["Difficulty_Level"],
                     "org": pr["Winter_Maintenance_Organization"], "sys": MOA_SEG.get((sysn, seg)) or MOA_SYS.get(sysn)}, pts)
    def near_official(pts):
        if not grid: return 0
        sm = densify(pts, 25); hit = 0
        for la, lo in sm:
            k = (round(la * 2500), round(lo * 1200)); found = False
            for dk in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
                for q in grid.get((k[0] + dk[0], k[1] + dk[1]), ()):
                    if length_km([(la, lo), q]) < 0.04: found = True; break
                if found: break
            hit += found
        return hit / len(sm)
    # 2. OpenStreetMap ski trails where the official layer doesn't reach
    heads = [p for p in skiplaces if p.get("lat") is not None]
    if os.path.exists("sources/osm-nordic.raw"):
        for e in json.load(open("sources/osm-nordic.raw"))["elements"]:
            if e["type"] != "way" or not e.get("geometry"): continue
            t = e.get("tags", {}); pts = [(g["lat"], g["lon"]) for g in e["geometry"]]
            if near_official(pts) > 0.6: continue
            mid = pts[len(pts) // 2]
            near = min(heads, key=lambda p: (p["lat"] - mid[0]) ** 2 + ((p["lon"] - mid[1]) * 0.48) ** 2) if heads else None
            if near and length_km([mid, (near["lat"], near["lon"])]) > 8: near = None
            add({"src": "osm", "s": style_of(t), "l": 1 if t.get("lit") == "yes" else 0,
                 "n": t.get("piste:name") or t.get("name") or "", "sys": near["id"] if near else None}, pts)
    # 3. named backcountry routes (ungroomed, or groomed by snowmachine at best)
    if os.path.exists("sources/osm-backcountry.raw"):
        for w in json.load(open("sources/osm-backcountry.raw")):
            pts = [(g["lat"], g["lon"]) for g in w["geometry"]]
            if len(pts) < 2: continue
            add({"src": "bc", "s": "bc", "l": 0, "n": w["route"], "sys": BC_SYS.get(w["route"])}, pts)
    for v in stats.values():
        for k in ("km", "lit", "classic", "skate", "skiOnly"): v[k] = round(v[k], 1)
    tracks = {"ways": out, "moaService": "https://services2.arcgis.com/Ce3DhLRthdwbHlfF/arcgis/rest/services/NordicTrails/FeatureServer/0"}
    return stats, tracks

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
