"""Backfill message_key + message_params on legacy OPEN alerts (created before
the structured-alert feature) so the dashboard can localize them via i18n.
Parses the English title/body of the 3 known alert templates. Idempotent:
only touches rows where message_key IS NULL."""
import os, re, json
import psycopg2

url = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
conn = psycopg2.connect(url)
conn.autocommit = False
cur = conn.cursor()

RE_ANOM_T = re.compile(r"^Anomaly detected: (?P<facility>.+)$")
RE_ANOM_B = re.compile(
    r"^Footfall (?P<direction>\w+) at .+?: (?P<latest>[\d.]+) visits vs "
    r"(?P<mean>[\d.]+) avg \(z=(?P<zscore>-?[\d.]+)\)"
)
RE_STOCK_T = re.compile(r"^Stockout risk: (?P<medicine>.+)$")
RE_STOCK_B = re.compile(
    r"^(?P<facility>.+?): .+ will run out in (?P<days>\d+) day\(s\)\. "
    r"Current stock (?P<stock>\d+) units \(reorder level (?P<reorder>\d+)\)\. "
    r"Confidence: (?P<confidence>[\d.]+)%"
)
RE_ATT_T = re.compile(r"^Zero doctor attendance: (?P<facility>.+)$")
RE_ATT_B = re.compile(r"for (?P<days>\d+)\+ consecutive days")

cur.execute("SELECT id, title, body FROM alerts WHERE status='OPEN' AND message_key IS NULL")
rows = cur.fetchall()

fnum = lambda s: float(s) if "." in s else int(s)
updates = []
unmatched = 0
for aid, title, body in rows:
    key = params = None
    if (m := RE_ANOM_T.match(title)):
        b = RE_ANOM_B.match(body or "")
        if b:
            key = "alert.anomaly"
            params = {"facility": m["facility"], "direction": b["direction"],
                      "latest": fnum(b["latest"]), "mean": fnum(b["mean"]),
                      "zscore": fnum(b["zscore"])}
    elif (m := RE_STOCK_T.match(title)):
        b = RE_STOCK_B.match(body or "")
        if b:
            key = "alert.stockout"
            params = {"facility": b["facility"], "medicine": m["medicine"],
                      "days": int(b["days"]), "stock": int(b["stock"]),
                      "reorder": int(b["reorder"]), "confidence": fnum(b["confidence"])}
    elif (m := RE_ATT_T.match(title)):
        b = RE_ATT_B.search(body or "")
        if b:
            key = "alert.attendance"
            params = {"facility": m["facility"], "days": int(b["days"])}
    if key:
        updates.append((key, json.dumps(params), aid))
    else:
        unmatched += 1

for key, pj, aid in updates:
    cur.execute("UPDATE alerts SET message_key=%s, message_params=%s::jsonb WHERE id=%s", (key, pj, aid))

conn.commit()
print(f"scanned={len(rows)} backfilled={len(updates)} unmatched={unmatched}")
cur.execute("SELECT COALESCE(message_key,'<null>'), count(*) FROM alerts WHERE status='OPEN' GROUP BY 1 ORDER BY 2 DESC")
for r in cur.fetchall():
    print(" ", r[0], r[1])
cur.close(); conn.close()
