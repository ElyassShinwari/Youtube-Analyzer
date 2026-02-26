import csv
import io
import json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template, request, jsonify, make_response, session
from analyzer import (
    load_api_key, resolve_channel, fetch_videos, analyze, analyze_extended,
    fmt_num, fmt_duration, safe_int, duration_to_seconds,
    compute_health_score, compute_seo_score, compute_revenue_estimate,
)
from googleapiclient.discovery import build
import storage
import alerts as alert_mod

app = Flask(__name__)
app.secret_key = "yt-analyzer-secret-2026"


# ── helpers ────────────────────────────────────────────────────────────────

def _views_dist(views_list):
    buckets = [0] * 7
    for v in views_list:
        if   v >= 1_000_000_000: buckets[0] += 1
        elif v >= 100_000_000:   buckets[1] += 1
        elif v >= 10_000_000:    buckets[2] += 1
        elif v >= 1_000_000:     buckets[3] += 1
        elif v >= 100_000:       buckets[4] += 1
        elif v >= 10_000:        buckets[5] += 1
        else:                    buckets[6] += 1
    return buckets


def _serialize_channel(channel, data, videos):
    snippet = channel.get("snippet", {})
    thumb   = snippet.get("thumbnails", {}).get("medium", {}).get("url", "")
    if not thumb:
        thumb = snippet.get("thumbnails", {}).get("default", {}).get("url", "")

    ext = analyze_extended(videos, data["avg_views"])

    top = []
    for v in data["top_videos"][:10]:
        s, st, cd = v["snippet"], v["statistics"], v["contentDetails"]
        avg = data["avg_views"] or 1
        views_raw = safe_int(st.get("viewCount"))
        pub = s.get("publishedAt", "")
        days_since = max((datetime.utcnow() - datetime.fromisoformat(
            pub.replace("Z",""))).days, 1) if pub else 1
        top.append({
            "title":        s.get("title", ""),
            "published":    pub[:10],
            "thumbnail":    s.get("thumbnails", {}).get("medium", {}).get("url", ""),
            "url":          f"https://youtube.com/watch?v={v['id']}",
            "duration":     fmt_duration(cd.get("duration", "PT0S")),
            "views":        fmt_num(views_raw),
            "views_raw":    views_raw,
            "likes":        fmt_num(safe_int(st.get("likeCount"))),
            "comments":     fmt_num(safe_int(st.get("commentCount"))),
            "viral_score":  round(views_raw / avg, 2),
            "velocity":     round(views_raw / days_since, 1),
            "seo_score":    compute_seo_score(s.get("title", "")),
        })

    # All videos for the Videos tab & CSV
    all_vids = []
    for v in videos:
        s, st, cd = v["snippet"], v["statistics"], v["contentDetails"]
        avg = data["avg_views"] or 1
        views_raw = safe_int(st.get("viewCount"))
        dur_iso = cd.get("duration", "PT0S")
        pub = s.get("publishedAt", "")
        days_since = max((datetime.utcnow() - datetime.fromisoformat(
            pub.replace("Z",""))).days, 1) if pub else 1
        all_vids.append({
            "title":        s.get("title", ""),
            "published":    pub[:10],
            "thumbnail":    s.get("thumbnails", {}).get("medium", {}).get("url", ""),
            "url":          f"https://youtube.com/watch?v={v['id']}",
            "duration":     fmt_duration(dur_iso),
            "duration_secs":duration_to_seconds(dur_iso),
            "views":        fmt_num(views_raw),
            "views_raw":    views_raw,
            "likes_raw":    safe_int(st.get("likeCount")),
            "comments_raw": safe_int(st.get("commentCount")),
            "likes":        fmt_num(safe_int(st.get("likeCount"))),
            "comments":     fmt_num(safe_int(st.get("commentCount"))),
            "viral_score":  round(views_raw / avg, 2),
            "velocity":     round(views_raw / days_since, 1),
            "seo_score":    compute_seo_score(s.get("title", "")),
            "is_short":     "Shorts" in v.get("snippet", {}).get("title", "")
                            or (cd.get("duration","") and
                                len(cd["duration"]) < 7 and "S" in cd["duration"]),
        })

    health_score  = compute_health_score(data, ext)
    revenue       = compute_revenue_estimate(data["avg_views"], data.get("avg_days_between") or 7)
    avg_seo_score = ext.get("avg_seo_score", 0)

    stats = channel.get("statistics", {})
    return {
        "channel_id":    channel["id"],
        "name":          data["channel_name"],
        "description":   data["description"],
        "country":       data["country"],
        "created_at":    data["created_at"],
        "thumbnail_url": thumb,
        "subscribers":          fmt_num(data["subscribers"]),
        "subscribers_raw":      data["subscribers"],
        "total_views":          fmt_num(data["total_views"]),
        "total_views_raw":      data["total_views"],
        "video_count":          f"{data['video_count']:,}",
        "video_count_raw":      data["video_count"],
        "avg_views":            fmt_num(data["avg_views"]),
        "avg_views_raw":        data["avg_views"],
        "avg_likes":            fmt_num(data["avg_likes"]),
        "avg_likes_raw":        data["avg_likes"],
        "avg_comments":         fmt_num(data["avg_comments"]),
        "avg_comments_raw":     data["avg_comments"],
        "eng_rate":             f"{data['eng_rate']:.2f}%",
        "eng_rate_raw":         data["eng_rate"],
        "upload_freq":          (f"every {data['avg_days_between']:.1f} days"
                                 if data["avg_days_between"] else "N/A"),
        "upload_freq_raw":      data["avg_days_between"] or 0,
        "upload_consistency":   ext["upload_consistency"],
        "top_videos":           top,
        "all_videos":           all_vids,
        "monthly_labels":       list(data["monthly"].keys()),
        "monthly_data":         list(data["monthly"].values()),
        "views_dist_labels":    ["1B+","100M+","10M+","1M+","100K+","10K+","<10K"],
        "views_dist_data":      _views_dist(data["views_list"]),
        "fetched_count":        data["fetched_count"],
        "extended":             ext,
        "health_score":         health_score,
        "revenue":              revenue,
        "avg_seo_score":        avg_seo_score,
    }


def _fetch_and_serialize(youtube, query):
    channel    = resolve_channel(youtube, query)
    uploads_id = (channel.get("contentDetails", {})
                         .get("relatedPlaylists", {})
                         .get("uploads"))
    if not uploads_id:
        raise ValueError("Could not find uploads playlist")
    videos = fetch_videos(youtube, uploads_id, max_videos=200)
    data   = analyze(channel, videos)
    result = _serialize_channel(channel, data, videos)

    # Persist snapshot + history
    try:
        storage.save_snapshot(
            channel["id"], data["channel_name"],
            data["subscribers"], data["total_views"],
            data["avg_views"], data["eng_rate"], data["video_count"]
        )
        storage.add_search(query, channel["id"], data["channel_name"], result["thumbnail_url"])
    except Exception:
        pass

    return result


# ── pages ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── analyze / compare ──────────────────────────────────────────────────────

@app.route("/analyze", methods=["POST"])
def analyze_channel():
    query = request.json.get("query", "").strip()
    if not query:
        return jsonify({"error": "No channel provided"}), 400
    try:
        api_key = load_api_key()
        youtube = build("youtube", "v3", developerKey=api_key)
        result  = _fetch_and_serialize(youtube, query)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _fetch_with_own_client(api_key, query):
    yt = build("youtube", "v3", developerKey=api_key)
    return _fetch_and_serialize(yt, query)


@app.route("/compare", methods=["POST"])
def compare_channels():
    q1 = request.json.get("query1", "").strip()
    q2 = request.json.get("query2", "").strip()
    if not q1 or not q2:
        return jsonify({"error": "Two channels required"}), 400
    try:
        api_key = load_api_key()
        with ThreadPoolExecutor(max_workers=2) as ex:
            f1 = ex.submit(_fetch_with_own_client, api_key, q1)
            f2 = ex.submit(_fetch_with_own_client, api_key, q2)
            d1, d2 = f1.result(), f2.result()
        return jsonify({"a": d1, "b": d2})
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── history ────────────────────────────────────────────────────────────────

@app.route("/history", methods=["GET"])
def get_history():
    return jsonify({"history": storage.get_history()})


@app.route("/history", methods=["DELETE"])
def clear_history():
    storage.clear_history()
    return jsonify({"ok": True})


# ── pinned channels ────────────────────────────────────────────────────────

@app.route("/pinned", methods=["GET"])
def get_pinned():
    return jsonify({"pinned": storage.get_pinned()})


@app.route("/pinned", methods=["POST"])
def pin_channel():
    d = request.json
    storage.pin_channel(d["channel_id"], d["channel_name"], d["query"], d.get("thumbnail_url",""))
    return jsonify({"ok": True})


@app.route("/pinned/<channel_id>", methods=["DELETE"])
def unpin_channel(channel_id):
    storage.unpin_channel(channel_id)
    return jsonify({"ok": True})


@app.route("/pinned/<channel_id>/status", methods=["GET"])
def pin_status(channel_id):
    return jsonify({"pinned": storage.is_pinned(channel_id)})


# ── export CSV ────────────────────────────────────────────────────────────

@app.route("/export/csv", methods=["POST"])
def export_csv():
    body    = request.json
    videos  = body.get("videos", [])
    name    = body.get("channel_name", "channel")

    output  = io.StringIO()
    writer  = csv.DictWriter(output, fieldnames=[
        "title","published","duration","views_raw","likes_raw",
        "comments_raw","viral_score"
    ])
    writer.writeheader()
    for v in videos:
        writer.writerow({
            "title":       v.get("title",""),
            "published":   v.get("published",""),
            "duration":    v.get("duration",""),
            "views_raw":   v.get("views_raw",0),
            "likes_raw":   v.get("likes_raw",0),
            "comments_raw":v.get("comments_raw",0),
            "viral_score": v.get("viral_score",0),
        })

    resp = make_response(output.getvalue())
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{name}_videos.csv"'
    return resp


# ── printable report ───────────────────────────────────────────────────────

@app.route("/report", methods=["POST"])
def print_report():
    data = request.json
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    return render_template("report.html", data=data, generated_at=generated_at)


# ── SMTP settings ──────────────────────────────────────────────────────────

@app.route("/settings/smtp", methods=["GET"])
def get_smtp():
    cfg = storage.get_smtp_config() or {}
    if cfg.get("password"):
        cfg["password"] = "••••••••"
    cfg["webhook_url"] = storage.get_webhook_url()
    return jsonify({"smtp": cfg})


@app.route("/settings/smtp", methods=["POST"])
def save_smtp():
    d = request.json
    # If password is masked placeholder, keep existing
    pwd = d.get("password","")
    if pwd == "••••••••":
        existing = storage.get_smtp_config()
        pwd = existing.get("password","") if existing else ""
    storage.save_smtp_config(
        d.get("host",""), int(d.get("port",587)),
        d.get("username",""), pwd,
        bool(d.get("use_tls", True)), d.get("from_addr","")
    )
    webhook_url = d.get("webhook_url", "")
    if webhook_url is not None:
        storage.save_webhook_url(webhook_url)
    return jsonify({"ok": True})


# ── alerts ─────────────────────────────────────────────────────────────────

@app.route("/alerts/<channel_id>", methods=["GET"])
def get_alert(channel_id):
    return jsonify({"config": storage.get_alert_config(channel_id)})


@app.route("/alerts/<channel_id>", methods=["POST"])
def set_alert(channel_id):
    d = request.json
    storage.set_alert_config(
        channel_id,
        d.get("email",""),
        float(d.get("spike_threshold", 50)),
        float(d.get("drop_threshold", 40)),
        d.get("check_frequency","daily"),
        bool(d.get("enabled", True))
    )
    return jsonify({"ok": True})


@app.route("/alerts/<channel_id>", methods=["DELETE"])
def delete_alert(channel_id):
    storage.delete_alert_config(channel_id)
    return jsonify({"ok": True})


@app.route("/alerts/test", methods=["POST"])
def test_alert():
    to = request.json.get("to_email","").strip()
    if not to:
        return jsonify({"error": "No email provided"}), 400
    try:
        alert_mod.send_test_email(to)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── leaderboard ────────────────────────────────────────────────────────────

@app.route("/leaderboard", methods=["POST"])
def leaderboard():
    queries = request.json.get("queries", [])
    if len(queries) < 2:
        return jsonify({"error": "At least 2 channels required"}), 400
    if len(queries) > 10:
        queries = queries[:10]
    try:
        api_key = load_api_key()
        with ThreadPoolExecutor(max_workers=min(len(queries), 5)) as ex:
            futures = [ex.submit(_fetch_with_own_client, api_key, q.strip())
                       for q in queries if q.strip()]
            channels = []
            for f in futures:
                try:
                    channels.append(f.result())
                except Exception as e:
                    channels.append({"error": str(e)})
        return jsonify({"channels": channels})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── growth tracker ─────────────────────────────────────────────────────────

@app.route("/growth/<channel_id>", methods=["GET"])
def growth(channel_id):
    snapshots = storage.get_all_snapshots(channel_id)
    return jsonify({"snapshots": snapshots})


# ── channel search ─────────────────────────────────────────────────────────

@app.route("/search-channels", methods=["POST"])
def search_channels():
    query = request.json.get("query", "").strip()
    if not query:
        return jsonify({"results": []})
    try:
        api_key = load_api_key()
        yt = build("youtube", "v3", developerKey=api_key)
        resp = yt.search().list(
            part="snippet", q=query, type="channel", maxResults=5
        ).execute()
        channel_ids = [item["id"]["channelId"] for item in resp.get("items", [])]
        if not channel_ids:
            return jsonify({"results": []})
        ch_resp = yt.channels().list(
            part="snippet,statistics", id=",".join(channel_ids)
        ).execute()
        results = []
        for ch in ch_resp.get("items", []):
            sn = ch.get("snippet", {})
            st = ch.get("statistics", {})
            results.append({
                "channel_id":  ch["id"],
                "name":        sn.get("title", ""),
                "thumbnail":   sn.get("thumbnails", {}).get("default", {}).get("url", ""),
                "subscribers": fmt_num(safe_int(st.get("subscriberCount"))),
                "handle":      sn.get("customUrl", ""),
            })
        return jsonify({"results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── tags ────────────────────────────────────────────────────────────────────

@app.route("/pinned/<channel_id>/tags", methods=["PUT"])
def update_tags(channel_id):
    tags = request.json.get("tags", "")
    storage.update_tags(channel_id, tags)
    return jsonify({"ok": True})


# ── PDF report ─────────────────────────────────────────────────────────────

@app.route("/report/pdf", methods=["POST"])
def report_pdf():
    try:
        import weasyprint
    except ImportError:
        return jsonify({"error": "weasyprint not installed"}), 500
    data = request.json
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    html = render_template("report_pdf.html", data=data, generated_at=generated_at)
    pdf_bytes = weasyprint.HTML(string=html).write_pdf()
    resp = make_response(pdf_bytes)
    resp.headers["Content-Type"] = "application/pdf"
    channel_name = data.get("name", "channel").replace(" ", "_")
    resp.headers["Content-Disposition"] = f'attachment; filename="{channel_name}_report.pdf"'
    return resp


# ── webhook test ────────────────────────────────────────────────────────────

@app.route("/alerts/test-webhook", methods=["POST"])
def test_webhook():
    url = request.json.get("webhook_url", "").strip()
    if not url:
        return jsonify({"error": "No webhook URL provided"}), 400
    try:
        alert_mod.post_webhook(url, {
            "text": "✅ YouTube Analyzer webhook test — connection successful!"
        })
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── startup check (spike detection on app open) ───────────────────────────

@app.route("/startup-check", methods=["GET"])
def startup_check():
    try:
        found = alert_mod.check_all_channels_for_alerts()
        return jsonify({"alerts": found})
    except Exception:
        return jsonify({"alerts": []})


# ── entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    app.run(port=args.port)
