"""
PIT-Audit ENTSO-E toplayıcısı (point-in-time / first-seen ölçümü)

Her çalıştırmada, her bölge x değişken x teslimat günü (bugün ve yarın, Brüksel saati)
için ENTSO-E API'sini sorgular. Seri ilk kez göründüğünde ya da değiştiğinde:
  - data/<COLLECTOR_ID>/events/YYYY-MM.csv dosyasına bir olay satırı,
  - data/<COLLECTOR_ID>/snapshots/... altına sıkıştırılmış anlık görüntü yazar.
Her çalıştırma data/<COLLECTOR_ID>/heartbeat/YYYY-MM.csv dosyasına bir satır ekler
(kesintisiz toplama kanıtı). Durum: state/<COLLECTOR_ID>.json.

Ortam değişkenleri:
  ENTSOE_API_KEY   (zorunlu)
  COLLECTOR_ID     (varsayılan: makine adı)   örn. gha, kku-local
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import hashlib
import json
import os
import socket
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

API = "https://web-api.tp.entsoe.eu/api"
BRU = ZoneInfo("Europe/Brussels")
UTC = dt.timezone.utc

ZONES = {
    "DE_LU": "10Y1001A1001A82H",
    "FR": "10YFR-RTE------C",
    "NL": "10YNL----------L",
    "BE": "10YBE----------2",
    "DK1": "10YDK-1--------W",
    "ES": "10YES-REE------0",
}

# değişken -> (sabit parametreler, bölge parametresi)
VARS = {
    "load_da":  ({"documentType": "A65", "processType": "A01"}, "outBiddingZone_Domain"),  # 6.1.B
    "gen_da":   ({"documentType": "A71", "processType": "A01"}, "in_Domain"),              # 14.1.C
    "ws_da":    ({"documentType": "A69", "processType": "A01"}, "in_Domain"),              # 14.1.D gün öncesi
    "ws_id":    ({"documentType": "A69", "processType": "A40"}, "in_Domain"),              # 14.1.D gün içi
    "ws_cur":   ({"documentType": "A69", "processType": "A18"}, "in_Domain"),              # 14.1.D güncel
    "price_da": ({"documentType": "A44"}, "in_out"),                                       # 12.1.D (kontrol)
}

ROOT = Path(__file__).resolve().parent
CID = os.environ.get("COLLECTOR_ID") or socket.gethostname()
DATA = ROOT / "data" / CID
STATE_FILE = ROOT / "state" / f"{CID}.json"
WORKERS = int(os.environ.get("WORKERS", "8"))


def now_utc() -> dt.datetime:
    return dt.datetime.now(UTC).replace(microsecond=0)


def iso(t: dt.datetime | None) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if t else ""


def day_window(d: dt.date) -> tuple[str, str]:
    s = dt.datetime.combine(d, dt.time(0), BRU).astimezone(UTC)
    e = dt.datetime.combine(d + dt.timedelta(days=1), dt.time(0), BRU).astimezone(UTC)
    return s.strftime("%Y%m%d%H%M"), e.strftime("%Y%m%d%H%M")


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child(el, name):
    for c in el:
        if local(c.tag) == name:
            return c
    return None


def parse(xml: bytes) -> list[tuple] | None:
    """Seriyi (psr, yön, çözünürlük, başlangıç_utc, değer) satırlarına çevirir.
    Veri yoksa None döner."""
    root = ET.fromstring(xml)
    if local(root.tag).startswith("Acknowledgement"):
        return None
    rows = []
    for ts in root.iter():
        if local(ts.tag) != "TimeSeries":
            continue
        psr, direction = "", "in"
        for c in ts.iter():
            n = local(c.tag)
            if n == "psrType":
                psr = c.text or ""
            elif n == "outBiddingZone_Domain.mRID":
                direction = "out"
        for per in ts:
            if local(per.tag) != "Period":
                continue
            ti = child(per, "timeInterval")
            start = dt.datetime.fromisoformat(child(ti, "start").text.replace("Z", "+00:00"))
            res = child(per, "resolution").text
            step = {"PT15M": 15, "PT30M": 30, "PT60M": 60}.get(res, 60)
            for pt in per:
                if local(pt.tag) != "Point":
                    continue
                pos = int(child(pt, "position").text)
                v = child(pt, "quantity")
                if v is None:
                    v = child(pt, "price.amount")
                t = start + dt.timedelta(minutes=step * (pos - 1))
                rows.append((psr, direction, res, iso(t), float(v.text)))
    rows.sort()
    return rows or None


def fetch(params: dict) -> tuple[int, bytes | None, str]:
    for attempt in range(3):
        try:
            r = requests.get(API, params=params, timeout=60)
        except requests.RequestException as e:  # ağ hatası
            err = f"{type(e).__name__}: {e}"
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            err = f"HTTP {r.status_code}"
            time.sleep(10 * (attempt + 1))
            continue
        return r.status_code, r.content, ""
    return -1, None, err


def append_csv(path: Path, header: list[str], row: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerow(row)


EVENT_HDR = ["poll_utc", "prev_poll_utc", "collector", "zone", "var", "delivery_date",
             "event", "n_points", "sha256", "snapshot", "http_status"]


def main() -> int:
    key = os.environ.get("ENTSOE_API_KEY")
    if not key:
        print("ENTSOE_API_KEY tanımlı değil", file=sys.stderr)
        return 2

    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    run_start = now_utc()
    month = run_start.strftime("%Y-%m")
    today = run_start.astimezone(BRU).date()
    days = [today, today + dt.timedelta(days=1)]
    n_req = n_ok = n_nodata = n_err = n_events = 0

    tasks = []
    for zone, code in ZONES.items():
        for var, (fixed, zparam) in VARS.items():
            for d in days:
                s, e = day_window(d)
                params = {"securityToken": key, "periodStart": s, "periodEnd": e, **fixed}
                if zparam == "in_out":
                    params["in_Domain"] = params["out_Domain"] = code
                else:
                    params[zparam] = code
                tasks.append((zone, var, d, params))

    def work(t):
        poll = now_utc()
        status, body, err = fetch(t[3])
        return t, poll, status, body, err

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        results = list(ex.map(work, tasks))

    for (zone, var, d, _), poll, status, body, err in results:
        k = f"{zone}|{var}|{d.isoformat()}"
        st = state.get(k, {"hash": None, "last_poll": None})
        n_req += 1
        if body is None or status not in (200, 400):
            n_err += 1
            append_csv(DATA / "errors" / f"{month}.csv",
                       ["poll_utc", "zone", "var", "delivery_date", "http_status", "error"],
                       [iso(poll), zone, var, d.isoformat(), status, err[:300]])
            continue  # hata: last_poll güncellenmez (gözlem yok)
        try:
            rows = parse(body)
        except ET.ParseError as ex_:
            n_err += 1
            append_csv(DATA / "errors" / f"{month}.csv",
                       ["poll_utc", "zone", "var", "delivery_date", "http_status", "error"],
                       [iso(poll), zone, var, d.isoformat(), status, f"ParseError {ex_}"])
            continue

        if rows is None:
            n_nodata += 1
            h = None
        else:
            n_ok += 1
            h = hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]

        if h != st["hash"]:
            event = ("first_seen" if st["hash"] is None else "revised") if h else "disappeared"
            snap = ""
            if rows:
                p = DATA / "snapshots" / var / zone / d.isoformat() / f"{poll.strftime('%Y%m%dT%H%M%SZ')}.csv.gz"
                p.parent.mkdir(parents=True, exist_ok=True)
                with gzip.open(p, "wt", newline="", encoding="utf-8") as f:
                    w = csv.writer(f)
                    w.writerow(["psr", "direction", "resolution", "start_utc", "value"])
                    w.writerows(rows)
                snap = p.relative_to(ROOT).as_posix()
            append_csv(DATA / "events" / f"{month}.csv", EVENT_HDR,
                       [iso(poll), st["last_poll"] or "", CID, zone, var, d.isoformat(),
                        event, len(rows or []), h or "", snap, status])
            n_events += 1
        state[k] = {"hash": h, "last_poll": iso(poll)}

    # 4 günden eski anahtarları buda
    cutoff = (today - dt.timedelta(days=4)).isoformat()
    state = {k: v for k, v in state.items() if k.split("|")[2] >= cutoff}
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=0, sort_keys=True))

    run_end = now_utc()
    append_csv(DATA / "heartbeat" / f"{month}.csv",
               ["run_start_utc", "run_end_utc", "collector", "n_requests", "n_ok",
                "n_nodata", "n_error", "n_events"],
               [iso(run_start), iso(run_end), CID, n_req, n_ok, n_nodata, n_err, n_events])
    print(f"{CID}: {n_req} istek, {n_ok} veri, {n_nodata} yok, {n_err} hata, "
          f"{n_events} olay, {(run_end - run_start).seconds} sn")
    return 0


if __name__ == "__main__":
    sys.exit(main())
