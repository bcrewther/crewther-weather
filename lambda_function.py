import os
import json
import csv
import boto3
import requests
import datetime
import zoneinfo
AUS_TZ = zoneinfo.ZoneInfo("Australia/Melbourne")
from io import StringIO

S3_BUCKET = os.environ.get("S3_BUCKET")
STATION_ID = os.environ.get("STATION_ID", "ITELAN2")
API_KEY = os.environ.get("API_KEY")
S3 = boto3.client("s3")

# Keys in S3
KEY_HOURLY = "hourly/hourly-observations.csv"
KEY_DAILY_RAIN = "daily/rainfall-history.csv"
KEY_DETAILED = "daily/detailed-daily.csv"
KEY_JSON = "rainfall-history.json"
KEY_SUMMARY = "summary.txt"
KEY_INDEX = "index.html"
KEY_INDEX_NEW = "indexnew.html"

API_URL_TEMPLATE = (
    "https://api.weather.com/v2/pws/observations/current"
    "?stationId={station}&format=json&units=m&apiKey={key}"
)

def fetch_current_obs(station_id, api_key):
    url = API_URL_TEMPLATE.format(station=station_id, key=api_key)
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    obs = None
    if "observations" in data and len(data["observations"]) > 0:
        obs = data["observations"][0]
    return obs or {}

def s3_get_text(key):
    try:
        obj = S3.get_object(Bucket=S3_BUCKET, Key=key)
        return obj["Body"].read().decode("utf-8")
    except S3.exceptions.NoSuchKey:
        return None

def s3_put_text(key, text, content_type="text/plain"):
    S3.put_object(Bucket=S3_BUCKET, Key=key, Body=text.encode("utf-8"), ContentType=content_type)

def append_row_to_csv_in_s3(key, headers, row):
    existing = s3_get_text(key)
    output = StringIO()
    writer = csv.writer(output)
    if existing is None:
        writer.writerow(headers)
        writer.writerow(row)
    else:
        output.write(existing)
        if not existing.endswith("\n"):
            output.write("\n")
        writer.writerow(row)
    s3_put_text(key, output.getvalue(), content_type="text/csv")

def generate_json_and_upload(daily_rows, detailed_rows):
    today = datetime.date.today().isoformat()
    latest = daily_rows[-1] if daily_rows else {}
    payload = {
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "station_id": STATION_ID,
        "last_update": today,
        "latest": latest,
        "rainfall_history": daily_rows,
        "detailed_history": detailed_rows[-365:]
    }
    s3_put_text(KEY_JSON, json.dumps(payload, indent=2), content_type="application/json")
    return payload

def compute_summary(daily_rows, detailed_rows):
    def parse_float(x):
        try: return float(x)
        except: return 0.0
    rows = daily_rows
    last_date = rows[-1]["date"] if rows else ""
    precip_latest = parse_float(rows[-1]["precip_mm"]) if rows else 0.0
    last7 = sum(parse_float(r["precip_mm"]) for r in rows[-7:]) if rows else 0.0
    last30 = sum(parse_float(r["precip_mm"]) for r in rows[-30:]) if rows else 0.0
    today = datetime.date.today()
    cur_month_rows = [r for r in rows if r["date"].startswith(today.strftime("%Y-%m"))]
    wettest_text = "N/A"
    if cur_month_rows:
        wettest = max(cur_month_rows, key=lambda r: parse_float(r["precip_mm"]))
        wettest_text = f'{wettest["precip_mm"]}mm ({wettest["date"]})'
    summary = [
        f"Station: {STATION_ID}",
        f"Generated: {datetime.datetime.utcnow().isoformat()}Z",
        f"Last update (date): {last_date}",
        f"Total rainfall (latest): {precip_latest} mm",
        f"Last 7 days total: {round(last7,3)} mm",
        f"Last 30 days total: {round(last30,3)} mm",
        f"Wettest day this month: {wettest_text}",
        "",
        "Recent daily rainfall (last 10 days):"
    ]
    for r in rows[-10:]:
        summary.append(f"  {r['date']}: {r['precip_mm']} mm")
    return "\n".join(summary)

def generate_index_html():
    html = f"""<!doctype html>
<html>
<head><meta charset="utf-8"/><title>The Blocks - Telangatuk East</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>body{{font-family:Arial,Helvetica,sans-serif;margin:16px;}}.chart-wrap{{max-width:900px;margin:auto;}}.header{{text-align:center;margin-bottom:8px;}}.links{{text-align:center;margin-top:16px;}}.links a{{margin:0 10px;text-decoration:none;color:#007BFF}}.links a:hover{{text-decoration:underline}}</style>
</head>
<body>
<div class="header"><h2>The Blocks - Telangatuk East</h2><p id="updated-at">Loading...</p></div>
<div class="chart-wrap"><canvas id="dailyRainChart" height="120"></canvas></div>
<div class="links">
<h3>Download / View Files:</h3>
<a href="https://{S3_BUCKET}.s3.amazonaws.com/{KEY_HOURLY}" target="_blank">Hourly CSV</a>
<a href="https://{S3_BUCKET}.s3.amazonaws.com/{KEY_DAILY_RAIN}" target="_blank">Daily CSV</a>
<a href="https://{S3_BUCKET}.s3.amazonaws.com/{KEY_DETAILED}" target="_blank">Detailed Daily CSV</a>
<a href="https://{S3_BUCKET}.s3.amazonaws.com/{KEY_JSON}" target="_blank">JSON Data</a>
<a href="https://{S3_BUCKET}.s3.amazonaws.com/{KEY_SUMMARY}" target="_blank">Summary</a>
<h3>Real time statistics:</h3>
<a href="https://www.wunderground.com/dashboard/pws/ITELAN2" target="_blank">Wunderground Dashboard</a>
</div>
<script>
async function loadAndRender() {{
  const resp = await fetch('{KEY_JSON}');
  const data = await resp.json();
  const hist = data.rainfall_history || [];
  const labels = hist.map(r => r.date);
  const values = hist.map(r => Number(r.precip_mm)||0);
  document.getElementById('updated-at').textContent =
    'Last updated: ' + data.last_update + ' (generated: ' + data.generated_at + ')';
  const ctx = document.getElementById('dailyRainChart').getContext('2d');
  new Chart(ctx, {{type:'bar',data:{{labels:labels,datasets:[{{label:'Daily rainfall (mm)',data:values,borderWidth:1}}]}},options:{{scales:{{x:{{display:true}},y:{{beginAtZero:true}}}}}}}});
}}
loadAndRender().catch(e=>{{document.getElementById('updated-at').textContent='Could not load data: '+(e&&e.message);}});
</script>
</body>
</html>"""
    return html

def generate_indexnew_html():
    html = f"""<!doctype html>
<html>
<head><meta charset="utf-8"/><title>The Blocks - Telangatuk East (Interactive)</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>body{{font-family:Arial,Helvetica,sans-serif;margin:16px;}}.chart-wrap{{max-width:900px;margin:auto;}}.header{{text-align:center;margin-bottom:8px;}}.links{{text-align:center;margin-top:16px;}}.links a{{margin:0 10px;text-decoration:none;color:#007BFF}}.links a:hover{{text-decoration:underline}}</style>
</head>
<body>
<div class="header"><h2>The Blocks - Telangatuk East</h2><p id="updated-at">Loading...</p></div>
<div><label for="viewSelect">Select view:</label><select id="viewSelect"><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option><option value="yearly">Yearly</option></select></div>
<div class="chart-wrap"><canvas id="rainChart" height="120"></canvas></div>
<div class="links">
<h3>Download / View Files:</h3>
<a href="https://{S3_BUCKET}.s3.amazonaws.com/{KEY_HOURLY}" target="_blank">Hourly CSV</a>
<a href="https://{S3_BUCKET}.s3.amazonaws.com/{KEY_DAILY_RAIN}" target="_blank">Daily CSV</a>
<a href="https://{S3_BUCKET}.s3.amazonaws.com/{KEY_DETAILED}" target="_blank">Detailed Daily CSV</a>
<a href="https://{S3_BUCKET}.s3.amazonaws.com/{KEY_JSON}" target="_blank">JSON Data</a>
<a href="https://{S3_BUCKET}.s3.amazonaws.com/{KEY_SUMMARY}" target="_blank">Summary</a>
<h3>Real time statistics:</h3>
<a href="https://www.wunderground.com/dashboard/pws/ITELAN2" target="_blank">Wunderground Dashboard</a>
</div>
<script>
let chartInstance;
async function loadAndRender(view='daily') {{
  const resp = await fetch('{KEY_JSON}');
  const data = await resp.json();
  const hist = data.rainfall_history || [];
  let labels=[],values=[];
  if(view==='daily'){{labels=hist.map(r=>r.date);values=hist.map(r=>Number(r.precip_mm)||0);}}
  else if(view==='weekly'){{let weekMap={{}};hist.forEach(r=>{{let w=r.date.slice(0,7)+'-W'+Math.ceil(parseInt(r.date.slice(-2))/7);weekMap[w]=(weekMap[w]||0)+Number(r.precip_mm||0);}});labels=Object.keys(weekMap);values=Object.values(weekMap);}}
  else if(view==='monthly'){{let monthMap={{}};hist.forEach(r=>{{let m=r.date.slice(0,7);monthMap[m]=(monthMap[m]||0)+Number(r.precip_mm||0);}});labels=Object.keys(monthMap);values=Object.values(monthMap);}}
  else if(view==='yearly'){{let yearMap={{}};hist.forEach(r=>{{let y=r.date.slice(0,4);yearMap[y]=(yearMap[y]||0)+Number(r.precip_mm||0);}});labels=Object.keys(yearMap);values=Object.values(yearMap);}}
  document.getElementById('updated-at').textContent='Last updated: '+data.last_update+' (generated: '+data.generated_at+')';
  const ctx=document.getElementById('rainChart').getContext('2d');
  if(chartInstance) chartInstance.destroy();
  chartInstance=new Chart(ctx,{{type:'bar',data:{{labels, datasets:[{{label:'Rainfall (mm)', data:values,borderWidth:1}}]}},options:{{scales:{{x:{{display:true}},y:{{beginAtZero:true}}}}}}}});
}}
document.getElementById('viewSelect').addEventListener('change', e=>{{loadAndRender(e.target.value);}});
loadAndRender().catch(e=>{{document.getElementById('updated-at').textContent='Could not load data: '+(e&&e.message);}});
</script>
</body>
</html>"""
    return html

def lambda_handler(event, context):
    if not S3_BUCKET: return {"statusCode":500,"body":"S3_BUCKET not configured"}
    if not API_KEY: return {"statusCode":500,"body":"API_KEY not configured"}

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_local = now_utc.astimezone(AUS_TZ)
    iso_ts = now_utc.isoformat().replace("+00:00","Z")
    date_today = now_local.date().isoformat()
    time_str = now_local.strftime("%H:%M:%S")

    obs = fetch_current_obs(STATION_ID, API_KEY)
    metric = obs.get("metric",{}) if isinstance(obs,dict) else {}
    precip_total = metric.get("precipTotal",0.0)
    try: precip_total=float(precip_total)
    except: precip_total=0.0
    temp = metric.get("temp")
    humidity = obs.get("humidity")
    wind_speed = metric.get("windSpeed")
    wind_gust_kph = metric.get("windGust")
    pressure = metric.get("pressure")
    dewpoint = metric.get("dewpt")

    # 1) Append hourly CSV
    headers_hourly=["timestamp_utc","date","time","precip_mm","temp_c","humidity","wind_speed","wind_gust_kph","pressure","dewpoint"]
    hourly_row=[iso_ts,date_today,time_str,f"{precip_total}",str(temp),str(humidity),str(wind_speed),str(wind_gust_kph),str(pressure),str(dewpoint)]
    append_row_to_csv_in_s3(KEY_HOURLY, headers_hourly, hourly_row)

    # 2) Update daily CSV (always latest cumulative total)
    existing_daily_txt = s3_get_text(KEY_DAILY_RAIN)
    daily_rows = []
    if existing_daily_txt:
        daily_rows = list(csv.DictReader(existing_daily_txt.splitlines()))
    found = False
    for r in daily_rows:
        if r.get("date") == date_today:
            r["precip_mm"] = f"{precip_total:.2f}"
            found = True
            break
    if not found:
        daily_rows.append({"date": date_today, "precip_mm": f"{precip_total:.2f}"})
    daily_rows = sorted(daily_rows, key=lambda r: r["date"])
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(["date","precip_mm"])
    for r in daily_rows:
        writer.writerow([r["date"], r["precip_mm"]])
    s3_put_text(KEY_DAILY_RAIN, out.getvalue(), content_type="text/csv")

    # 3) Update detailed CSV (with delta since last hour)
    existing_det_txt = s3_get_text(KEY_DETAILED)
    det_rows = []
    if existing_det_txt: det_rows=list(csv.DictReader(existing_det_txt.splitlines()))
    prev_precip = 0.0
    if det_rows:
        last_row = det_rows[-1]
        try: prev_precip = float(last_row.get("precip_mm",0.0))
        except: prev_precip=0.0
    hourly_delta = precip_total - prev_precip
    if hourly_delta < 0: hourly_delta = precip_total
    detailed_row = {
        "date":date_today,
        "precip_mm":f"{precip_total:.2f}",
        "hourly_delta":f"{hourly_delta:.2f}",
        "temp_c":str(temp),
        "humidity":str(humidity),
        "wind_speed":str(wind_speed),
        "wind_gust_kph":str(wind_gust_kph),
        "pressure":str(pressure),
        "dewpoint":str(dewpoint),
        "last_update_utc":iso_ts
    }
    replaced=False
    for i,r in enumerate(det_rows):
        if r.get("date")==date_today:
            det_rows[i]=detailed_row
            replaced=True
            break
    if not replaced: det_rows.append(detailed_row)
    det_rows=sorted(det_rows,key=lambda r:r["date"])
    out_det=StringIO()
    headers_det=["date","precip_mm","hourly_delta","temp_c","humidity","wind_speed","wind_gust_kph","pressure","dewpoint","last_update_utc"]
    writer=csv.writer(out_det)
    writer.writerow(headers_det)
    for r in det_rows: writer.writerow([r.get(h,"") for h in headers_det])
    s3_put_text(KEY_DETAILED,out_det.getvalue(),content_type="text/csv")

    # 4) Generate JSON
    json_payload = generate_json_and_upload(daily_rows, det_rows)

    # 5) Generate summary
    summary_text = compute_summary(daily_rows, det_rows)
    s3_put_text(KEY_SUMMARY, summary_text, content_type="text/plain")

    # 6) Generate dashboards
    s3_put_text(KEY_INDEX, generate_index_html(), content_type="text/html")
    s3_put_text(KEY_INDEX_NEW, generate_indexnew_html(), content_type="text/html")

    return {
        "statusCode":200,
        "body":json.dumps({
            "message":"Updated hourly + daily files and dashboards",
            "public_files":{
                "hourly":f"https://{S3_BUCKET}.s3.amazonaws.com/{KEY_HOURLY}",
                "daily_csv":f"https://{S3_BUCKET}.s3.amazonaws.com/{KEY_DAILY_RAIN}",
                "detailed_daily_csv":f"https://{S3_BUCKET}.s3.amazonaws.com/{KEY_DETAILED}",
                "json":f"https://{S3_BUCKET}.s3.amazonaws.com/{KEY_JSON}",
                "summary":f"https://{S3_BUCKET}.s3.amazonaws.com/{KEY_SUMMARY}",
                "dashboard":f"https://{S3_BUCKET}.s3.amazonaws.com/{KEY_INDEX}",
                "dashboard_new":f"https://{S3_BUCKET}.s3.amazonaws.com/{KEY_INDEX_NEW}"
            }
        })
    }
