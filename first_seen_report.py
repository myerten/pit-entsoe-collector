"""
İlk görülme raporu: her bölge x değişken x teslimat günü için gün öncesi verinin
SDAC kapanışına (D-1 12:00 Brüksel) göre ne zaman ilk görüldüğünü özetler.

Tüm toplayıcıların (data/*/events) olaylarını birleştirir. Bir anahtar için:
  first_seen  = en erken 'first_seen' sorgu zamanı (tüm toplayıcılar)
  bracket_lo  = o olaydan önceki son 'yok' sorgusu (aynı toplayıcı) -> yayım (bracket_lo, first_seen] arasında
  n_revisions = ilk görülmeden sonraki 'revised' olay sayısı
Sol-sansürlü (toplama başlamadan önce zaten var olan) anahtarlar 'censored' olarak işaretlenir.

Kullanım:  python first_seen_report.py [--gate 11:45]
Çıktı:     reports/first_seen.csv ve ekranda bölge x değişken özeti
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

BRU = ZoneInfo("Europe/Brussels")
ROOT = Path(__file__).resolve().parent


def parse_t(s: str):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", default="11:45", help="Brüksel saatiyle D-1 eşik (varsayılan 11:45)")
    a = ap.parse_args()
    gh, gm = map(int, a.gate.split(":"))

    ev = defaultdict(list)
    for f in sorted(ROOT.glob("data/*/events/*.csv")):
        with f.open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                ev[(r["zone"], r["var"], r["delivery_date"])].append(r)

    out = []
    for (zone, var, dd), rows in sorted(ev.items()):
        d = dt.date.fromisoformat(dd)
        gate = dt.datetime.combine(d - dt.timedelta(days=1), dt.time(gh, gm), BRU)
        firsts = [r for r in rows if r["event"] == "first_seen"]
        if not firsts:
            continue
        f0 = min(firsts, key=lambda r: r["poll_utc"])
        fs = parse_t(f0["poll_utc"])
        lo = parse_t(f0["prev_poll_utc"])
        censored = lo is None
        revs = [r for r in rows if r["event"] == "revised" and r["poll_utc"] > f0["poll_utc"]]
        revs_after_gate = [r for r in revs if parse_t(r["poll_utc"]) > gate]
        if censored:
            status = "censored"
        elif fs <= gate:
            status = "before_gate"
        elif lo >= gate:
            status = "after_gate"
        else:
            status = "straddles_gate"
        out.append({
            "zone": zone, "var": var, "delivery_date": dd,
            "first_seen_local": fs.astimezone(BRU).strftime("%Y-%m-%d %H:%M"),
            "bracket_lo_local": lo.astimezone(BRU).strftime("%Y-%m-%d %H:%M") if lo else "",
            "status": status, "n_revisions": len(revs),
            "n_revisions_after_gate": len(revs_after_gate),
        })

    (ROOT / "reports").mkdir(exist_ok=True)
    p = ROOT / "reports" / "first_seen.csv"
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0].keys()) if out else ["empty"])
        w.writeheader()
        w.writerows(out)

    agg = defaultdict(lambda: defaultdict(int))
    for r in out:
        agg[(r["zone"], r["var"])][r["status"]] += 1
        agg[(r["zone"], r["var"])]["rev_after_gate"] += r["n_revisions_after_gate"]
    print(f"Eşik: D-1 {a.gate} Brüksel  |  {len(out)} anahtar  ->  {p}")
    print(f"{'bölge':6} {'değişken':9} {'önce':>5} {'sonra':>5} {'arada':>5} {'sansür':>6} {'kap.sonrası rev.':>16}")
    for (z, v), c in sorted(agg.items()):
        print(f"{z:6} {v:9} {c['before_gate']:5} {c['after_gate']:5} {c['straddles_gate']:5} "
              f"{c['censored']:6} {c['rev_after_gate']:16}")


if __name__ == "__main__":
    main()
