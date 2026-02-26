"""
Email alerts and spike/drop detection for YouTube Channel Analyzer.
Uses smtplib (stdlib). SMTP config stored in SQLite via storage.py.
"""
import smtplib
import subprocess
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

import storage


def _get_smtp():
    cfg = storage.get_smtp_config()
    if not cfg or not cfg.get("host"):
        raise RuntimeError("SMTP not configured. Go to Settings → Email Settings.")
    return cfg


def send_email(to: str, subject: str, html_body: str):
    """Send an HTML email. Raises on failure."""
    cfg = _get_smtp()
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = cfg.get("from_addr") or cfg["username"]
    msg["To"]      = to
    msg.attach(MIMEText(html_body, "html"))

    if cfg.get("use_tls", 1):
        server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=10)
        server.starttls()
    else:
        server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=10)

    server.login(cfg["username"], cfg["password"])
    server.sendmail(msg["From"], [to], msg.as_string())
    server.quit()


def send_test_email(to: str):
    send_email(to, "YouTube Analyzer — Test Email",
               "<h2>✅ Email is working!</h2>"
               "<p>Your YouTube Channel Analyzer alert emails are configured correctly.</p>")


def send_email_with_attachment(to: str, subject: str, html_body: str,
                                attachment_bytes: bytes, filename: str):
    """Send an HTML email with a binary attachment."""
    cfg = _get_smtp()
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"]    = cfg.get("from_addr") or cfg["username"]
    msg["To"]      = to
    msg.attach(MIMEText(html_body, "html"))

    part = MIMEBase("application", "pdf")
    part.set_payload(attachment_bytes)
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
    msg.attach(part)

    if cfg.get("use_tls", 1):
        server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=10)
        server.starttls()
    else:
        server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=10)
    server.login(cfg["username"], cfg["password"])
    server.sendmail(msg["From"], [to], msg.as_string())
    server.quit()


def post_webhook(url: str, payload: dict) -> None:
    """Post a JSON payload to a webhook URL. Silent on failure."""
    if not url or not _HAS_REQUESTS:
        return
    try:
        _requests.post(url, json=payload, timeout=5)
    except Exception:
        pass


def check_new_videos(api_key: str) -> list[dict]:
    """
    For each pinned channel, check if a new video has been uploaded since last check.
    Returns list of {channel_name, video_title, video_url} for new videos found.
    """
    from googleapiclient.discovery import build

    pinned = storage.get_pinned_last_video_ids()
    found  = []
    webhook_url = storage.get_webhook_url()

    for p in pinned:
        channel_id    = p["channel_id"]
        channel_name  = p.get("channel_name", channel_id)
        stored_vid_id = p.get("last_video_id", "") or ""

        try:
            yt = build("youtube", "v3", developerKey=api_key)
            # Get uploads playlist
            ch_resp = yt.channels().list(
                part="contentDetails", id=channel_id
            ).execute()
            ch_items = ch_resp.get("items", [])
            if not ch_items:
                continue
            uploads_id = (ch_items[0].get("contentDetails", {})
                          .get("relatedPlaylists", {}).get("uploads"))
            if not uploads_id:
                continue

            pl_resp = yt.playlistItems().list(
                part="contentDetails,snippet", playlistId=uploads_id, maxResults=1
            ).execute()
            pl_items = pl_resp.get("items", [])
            if not pl_items:
                continue

            latest = pl_items[0]
            vid_id    = latest["contentDetails"].get("videoId", "")
            vid_title = latest["snippet"].get("title", "")
            vid_url   = f"https://youtube.com/watch?v={vid_id}"

            if vid_id and vid_id != stored_vid_id:
                storage.update_last_video_id(channel_id, vid_id)
                found.append({"channel_name": channel_name,
                               "video_title": vid_title, "video_url": vid_url})
                notify_desktop(channel_name, f"New video: {vid_title}")
                # Try email alert
                cfg = storage.get_alert_config(channel_id)
                if cfg and cfg.get("email"):
                    try:
                        body = (f"<h2>New video from {channel_name}</h2>"
                                f"<p><a href='{vid_url}'>{vid_title}</a></p>")
                        send_email(cfg["email"],
                                   f"📹 New video: {channel_name}", body)
                    except Exception:
                        pass
                # Webhook
                if webhook_url:
                    post_webhook(webhook_url, {
                        "text": f"📹 New video from {channel_name}: {vid_title} — {vid_url}"
                    })
        except Exception:
            continue

    return found


def send_weekly_pdf_reports() -> None:
    """For each pinned channel with an alert config, email a weekly PDF report."""
    from flask import current_app

    configs = storage.get_all_alert_configs()
    for cfg in configs:
        channel_id = cfg["channel_id"]
        to_email   = cfg.get("email", "")
        if not to_email:
            continue
        snaps = storage.get_latest_snapshots(channel_id, 1)
        if not snaps:
            continue
        curr = snaps[0]
        channel_name = curr.get("channel_name", channel_id)
        try:
            import weasyprint
            from flask import render_template
            html = render_template("report_pdf.html",
                                   data=curr,
                                   generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"))
            pdf_bytes = weasyprint.HTML(string=html).write_pdf()
            send_email_with_attachment(
                to_email,
                f"📊 Weekly Report: {channel_name}",
                f"<p>Weekly channel report for <b>{channel_name}</b> attached as PDF.</p>",
                pdf_bytes,
                f"{channel_name}_report.pdf"
            )
        except Exception:
            # Fall back to HTML-only email if weasyprint unavailable
            try:
                body = _make_report_email(channel_name, curr)
                send_email(to_email, f"📊 Weekly Report: {channel_name}", body)
            except Exception:
                pass


def notify_desktop(title: str, body: str, urgency: str = "normal"):
    """Show a desktop notification via notify-send (Linux). Silent on failure."""
    try:
        subprocess.run(
            ["notify-send", "-u", urgency, "-i", "dialog-information",
             f"YouTube Analyzer — {title}", body],
            timeout=3, capture_output=True
        )
    except Exception:
        pass


def detect_spikes(prev: dict, curr: dict,
                  spike_pct: float = 50.0,
                  drop_pct: float  = 40.0) -> list[dict]:
    """
    Compare two snapshots. Return list of alert dicts for each metric that
    changed beyond the threshold.
    """
    alerts = []
    metrics = [
        ("avg_views",    "Avg Views/Video"),
        ("subscribers",  "Subscribers"),
        ("eng_rate",     "Engagement Rate"),
    ]
    for key, label in metrics:
        p, c = prev.get(key, 0), curr.get(key, 0)
        if p == 0:
            continue
        pct = (c - p) / p * 100
        if pct >= spike_pct:
            alerts.append({"type": "spike", "metric": label,
                            "previous": p, "current": c, "pct_change": round(pct, 1)})
        elif pct <= -drop_pct:
            alerts.append({"type": "drop", "metric": label,
                            "previous": p, "current": c, "pct_change": round(pct, 1)})
    return alerts


def _make_alert_email(channel_name: str, alerts_list: list[dict], curr: dict) -> str:
    rows = ""
    for a in alerts_list:
        icon  = "📈" if a["type"] == "spike" else "📉"
        color = "#22c55e" if a["type"] == "spike" else "#ef4444"
        rows += (f"<tr><td style='padding:8px;border-bottom:1px solid #222'>{icon} {a['metric']}</td>"
                 f"<td style='padding:8px;border-bottom:1px solid #222;color:{color};font-weight:700'>"
                 f"{a['pct_change']:+.1f}%</td>"
                 f"<td style='padding:8px;border-bottom:1px solid #222;color:#888'>"
                 f"{a['previous']} → {a['current']}</td></tr>")

    return f"""
    <div style="font-family:Inter,sans-serif;background:#0f0f0f;padding:32px;border-radius:12px;max-width:520px">
      <h2 style="color:#ff4444;margin:0 0 4px">YouTube Analyzer Alert</h2>
      <h3 style="color:#fff;margin:0 0 20px;font-weight:500">{channel_name}</h3>
      <table style="width:100%;border-collapse:collapse;color:#e0e0e0;font-size:14px">
        <thead><tr>
          <th style="text-align:left;padding:8px;color:#888;border-bottom:1px solid #333">Metric</th>
          <th style="text-align:left;padding:8px;color:#888;border-bottom:1px solid #333">Change</th>
          <th style="text-align:left;padding:8px;color:#888;border-bottom:1px solid #333">Values</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
      <p style="color:#555;font-size:12px;margin-top:20px">
        Detected: {datetime.now().strftime('%Y-%m-%d %H:%M')} ·
        Subscribers: {curr.get('subscribers',0):,} ·
        Avg Views: {curr.get('avg_views',0):,.0f}
      </p>
    </div>"""


def _make_report_email(channel_name: str, curr: dict) -> str:
    return f"""
    <div style="font-family:Inter,sans-serif;background:#0f0f0f;padding:32px;border-radius:12px;max-width:520px">
      <h2 style="color:#ff0000;margin:0 0 4px">📊 Channel Report</h2>
      <h3 style="color:#fff;margin:0 0 20px;font-weight:500">{channel_name}</h3>
      <table style="width:100%;border-collapse:collapse;color:#e0e0e0;font-size:14px">
        <tbody>
          {''.join(f"<tr><td style='padding:8px;border-bottom:1px solid #222;color:#888'>{k}</td>"
                   f"<td style='padding:8px;border-bottom:1px solid #222;font-weight:600'>{v}</td></tr>"
                   for k,v in [
                     ('Subscribers', f"{curr.get('subscribers',0):,}"),
                     ('Total Views', f"{curr.get('total_views',0):,}"),
                     ('Avg Views/Video', f"{curr.get('avg_views',0):,.0f}"),
                     ('Engagement Rate', f"{curr.get('eng_rate',0):.2f}%"),
                     ('Videos', f"{curr.get('video_count',0):,}"),
                   ])}
        </tbody>
      </table>
      <p style="color:#555;font-size:12px;margin-top:20px">
        Report generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}
      </p>
    </div>"""


def check_all_channels_for_alerts(frequency_filter: str = None) -> list[dict]:
    """
    For each pinned channel with an alert config, compare last 2 snapshots.
    Sends email + desktop notification on spikes/drops.
    Returns list of all alerts found.
    """
    configs   = storage.get_all_alert_configs()
    all_found = []

    for cfg in configs:
        if frequency_filter and cfg["check_frequency"] != frequency_filter:
            continue

        channel_id = cfg["channel_id"]
        to_email   = cfg["email"]
        snaps      = storage.get_latest_snapshots(channel_id, 2)

        if len(snaps) < 2:
            # Only one snapshot — no comparison yet, but send report if scheduled
            if snaps:
                try:
                    body = _make_report_email(snaps[0].get("channel_name",""), snaps[0])
                    send_email(to_email, f"📊 YouTube Report: {snaps[0].get('channel_name','')}", body)
                except Exception:
                    pass
            continue

        curr, prev = snaps[0], snaps[1]
        channel_name = curr.get("channel_name", channel_id)
        alerts = detect_spikes(prev, curr, cfg["spike_threshold"], cfg["drop_threshold"])

        webhook_url = storage.get_webhook_url()

        if alerts:
            all_found.extend([{**a, "channel_name": channel_name, "channel_id": channel_id}
                               for a in alerts])
            try:
                body = _make_alert_email(channel_name, alerts, curr)
                send_email(to_email, f"⚠️ Alert: {channel_name}", body)
            except Exception:
                pass
            for a in alerts:
                notify_desktop(
                    channel_name,
                    f"{a['metric']} {a['type']}: {a['pct_change']:+.1f}%",
                    urgency="critical" if a["type"] == "drop" else "normal"
                )
                if webhook_url:
                    post_webhook(webhook_url, {
                        "text": (f"⚠️ Channel: {channel_name} — "
                                 f"{a['metric']} {a['type']} {a['pct_change']:+.1f}%")
                    })
        else:
            # No spike — send scheduled report
            try:
                body = _make_report_email(channel_name, curr)
                send_email(to_email, f"📊 YouTube Report: {channel_name}", body)
            except Exception:
                pass

    return all_found
