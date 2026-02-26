"""
YouTube Channel Analyzer
Usage:
    python analyzer.py <channel_url_or_handle_or_id>
    python analyzer.py @MrBeast
    python analyzer.py https://www.youtube.com/@veritasium
    python analyzer.py UCxxxxxxxxxxxxxxxxxxxxxx
"""

import sys
import os
import re
import statistics
import calendar
from datetime import datetime, timezone
from collections import defaultdict
from dotenv import load_dotenv
from googleapiclient.discovery import build
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich import box
from rich.text import Text

console = Console()

# ── helpers ──────────────────────────────────────────────────────────────────

def load_api_key() -> str:
    load_dotenv()
    key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not key:
        console.print("[red]Error:[/] YOUTUBE_API_KEY not found. "
                      "Create a .env file with YOUTUBE_API_KEY=your_key")
        sys.exit(1)
    return key


def fmt_num(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def fmt_duration(iso: str) -> str:
    """Convert ISO 8601 duration (PT4M13S) to MM:SS or HH:MM:SS."""
    h = re.search(r"(\d+)H", iso)
    m = re.search(r"(\d+)M", iso)
    s = re.search(r"(\d+)S", iso)
    hours   = int(h.group(1)) if h else 0
    minutes = int(m.group(1)) if m else 0
    seconds = int(s.group(1)) if s else 0
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def safe_int(v, default=0) -> int:
    try:
        return int(v)
    except Exception:
        return default


# ── channel resolution ───────────────────────────────────────────────────────

CHANNEL_ID_RE = re.compile(r"UC[a-zA-Z0-9_-]{20,}")


def resolve_channel(youtube, query: str) -> dict:
    """Return channel resource given a URL, handle, channel ID, or search term."""
    query = query.strip()

    # Direct channel ID
    if CHANNEL_ID_RE.match(query):
        return _fetch_channel_by_id(youtube, query)

    # Handle / URL containing @
    if "@" in query:
        handle = re.split(r"/@|@", query, 1)[-1].split("/")[0].strip()
        return _fetch_channel_by_handle(youtube, handle)

    # Old /user/ style
    if "/user/" in query.lower():
        username = query.lower().split("/user/")[1].split("/")[0]
        return _fetch_channel_by_username(youtube, username)

    # Fallback: search
    return _search_channel(youtube, query)


def _fetch_channel_by_id(youtube, channel_id: str) -> dict:
    resp = youtube.channels().list(
        part="snippet,statistics,contentDetails,brandingSettings",
        id=channel_id
    ).execute()
    items = resp.get("items", [])
    if not items:
        raise ValueError(f"No channel found for ID: {channel_id}")
    return items[0]


def _fetch_channel_by_handle(youtube, handle: str) -> dict:
    resp = youtube.channels().list(
        part="snippet,statistics,contentDetails,brandingSettings",
        forHandle=handle
    ).execute()
    items = resp.get("items", [])
    if not items:
        raise ValueError(f"No channel found for handle: @{handle}")
    return items[0]


def _fetch_channel_by_username(youtube, username: str) -> dict:
    resp = youtube.channels().list(
        part="snippet,statistics,contentDetails,brandingSettings",
        forUsername=username
    ).execute()
    items = resp.get("items", [])
    if not items:
        raise ValueError(f"No channel found for username: {username}")
    return items[0]


def _search_channel(youtube, query: str) -> dict:
    resp = youtube.search().list(
        part="snippet", q=query, type="channel", maxResults=1
    ).execute()
    items = resp.get("items", [])
    if not items:
        raise ValueError(f"No channel found for query: {query}")
    channel_id = items[0]["id"]["channelId"]
    return _fetch_channel_by_id(youtube, channel_id)


# ── video fetching ───────────────────────────────────────────────────────────

def fetch_videos(youtube, uploads_playlist_id: str, max_videos: int = 200) -> list[dict]:
    """Fetch video IDs from the uploads playlist, then get full stats."""
    video_ids = []
    next_page = None

    while len(video_ids) < max_videos:
        kwargs = dict(
            part="contentDetails",
            playlistId=uploads_playlist_id,
            maxResults=min(50, max_videos - len(video_ids)),
        )
        if next_page:
            kwargs["pageToken"] = next_page

        resp = youtube.playlistItems().list(**kwargs).execute()
        for item in resp.get("items", []):
            vid_id = item["contentDetails"].get("videoId")
            if vid_id:
                video_ids.append(vid_id)

        next_page = resp.get("nextPageToken")
        if not next_page:
            break

    # Fetch full stats in batches of 50
    videos = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i+50]
        resp = youtube.videos().list(
            part="snippet,statistics,contentDetails",
            id=",".join(batch)
        ).execute()
        videos.extend(resp.get("items", []))

    return videos


# ── analytics ────────────────────────────────────────────────────────────────

def analyze(channel: dict, videos: list[dict]) -> dict:
    stats = channel.get("statistics", {})
    snippet = channel.get("snippet", {})

    total_views   = safe_int(stats.get("viewCount"))
    subscribers   = safe_int(stats.get("subscriberCount"))
    video_count   = safe_int(stats.get("videoCount"))
    channel_name  = snippet.get("title", "Unknown")
    description   = snippet.get("description", "")[:200]
    country       = snippet.get("country", "N/A")
    created_at    = snippet.get("publishedAt", "")[:10]

    # Per-video metrics
    views_list  = [safe_int(v["statistics"].get("viewCount"))  for v in videos]
    likes_list  = [safe_int(v["statistics"].get("likeCount"))  for v in videos]
    comment_list= [safe_int(v["statistics"].get("commentCount"))for v in videos]

    avg_views    = int(sum(views_list)  / len(views_list))   if views_list  else 0
    avg_likes    = int(sum(likes_list)  / len(likes_list))   if likes_list  else 0
    avg_comments = int(sum(comment_list)/ len(comment_list)) if comment_list else 0

    # Engagement rate = (likes + comments) / views * 100
    total_v = sum(views_list) or 1
    eng_rate = (sum(likes_list) + sum(comment_list)) / total_v * 100

    # Upload frequency (days between uploads, using fetched videos)
    dates = []
    for v in videos:
        pub = v["snippet"].get("publishedAt", "")
        if pub:
            dates.append(datetime.fromisoformat(pub.replace("Z", "+00:00")))
    dates.sort(reverse=True)

    avg_days_between = None
    if len(dates) >= 2:
        deltas = [(dates[i] - dates[i+1]).days for i in range(len(dates)-1)]
        avg_days_between = sum(deltas) / len(deltas)

    # Top 10 by views
    top_videos = sorted(videos, key=lambda v: safe_int(v["statistics"].get("viewCount")), reverse=True)[:10]

    # Monthly upload counts
    monthly = defaultdict(int)
    for v in videos:
        pub = v["snippet"].get("publishedAt", "")
        if pub:
            ym = pub[:7]  # "YYYY-MM"
            monthly[ym] += 1

    return dict(
        channel_name=channel_name,
        description=description,
        country=country,
        created_at=created_at,
        subscribers=subscribers,
        total_views=total_views,
        video_count=video_count,
        fetched_count=len(videos),
        avg_views=avg_views,
        avg_likes=avg_likes,
        avg_comments=avg_comments,
        eng_rate=eng_rate,
        avg_days_between=avg_days_between,
        top_videos=top_videos,
        monthly=dict(sorted(monthly.items(), reverse=True)[:12]),
        views_list=views_list,
    )


# ── new compute functions ────────────────────────────────────────────────────

def compute_health_score(data: dict, ext: dict) -> dict:
    """Score 0–100 with grade A/B/C/D based on 5 dimensions."""
    eng_rate         = data.get("eng_rate", 0)
    upload_cons      = ext.get("upload_consistency") or 30
    viral_videos     = ext.get("viral_videos", [])
    fetched_count    = data.get("fetched_count", 1) or 1
    subscribers      = data.get("subscribers", 0)
    total_views      = data.get("total_views", 1) or 1
    views_over_time  = ext.get("views_over_time", [])

    # Recency: days since most recent upload
    days_since_recent = 999
    if views_over_time:
        try:
            latest_date = max(v["date"] for v in views_over_time)
            days_since_recent = (datetime.now(timezone.utc) -
                                 datetime.fromisoformat(latest_date).replace(tzinfo=timezone.utc)
                                 ).days
        except Exception:
            pass

    eng_pts   = min(eng_rate / 5.0 * 30, 30)
    cons_pts  = max(0, 20 - upload_cons / 1.5)
    viral_pts = min(len(viral_videos) / fetched_count * 200, 20)
    sv_pts    = min(subscribers / total_views * 10000, 15)
    rec_pts   = max(0, 15 - days_since_recent / 3)

    score = int(eng_pts + cons_pts + viral_pts + sv_pts + rec_pts)
    score = max(0, min(100, score))
    if score >= 80:   grade = "A"
    elif score >= 60: grade = "B"
    elif score >= 40: grade = "C"
    else:             grade = "D"

    return {
        "score":       score,
        "grade":       grade,
        "engagement":  round(eng_pts, 1),
        "consistency": round(cons_pts, 1),
        "viral":       round(viral_pts, 1),
        "sub_view":    round(sv_pts, 1),
        "recency":     round(rec_pts, 1),
    }


_POWER_WORDS = {"best", "top", "how", "why", "secret", "ultimate", "guide", "tips", "vs"}


def compute_seo_score(title: str) -> int:
    """Score a video title 0–100 for SEO quality."""
    score = 0
    if re.search(r"\d", title):              score += 15
    if "?" in title:                         score += 10
    if 40 <= len(title) <= 70:               score += 20
    words = set(re.sub(r"[^a-zA-Z0-9 ]", " ", title).lower().split())
    if words & _POWER_WORDS:                 score += 15
    if re.search(r"\b(19|20)\d{2}\b", title): score += 10
    if re.search(r"\b[A-Z]{2,}\b", title):  score -= 10
    if len(title) > 80:                      score -= 10
    return max(0, min(100, score))


def compute_revenue_estimate(avg_views: float, avg_days_between: float) -> dict:
    """Rough CPM-based monthly revenue estimate."""
    def fmt_dollar(n: float) -> str:
        if n >= 1000:
            return f"${n/1000:.1f}K"
        return f"${n:.0f}"

    monthly_uploads = 30 / max(avg_days_between or 7, 0.5)
    monthly_views   = avg_views * monthly_uploads
    return {
        "low":  fmt_dollar(monthly_views * 1 / 1000),
        "mid":  fmt_dollar(monthly_views * 3 / 1000),
        "high": fmt_dollar(monthly_views * 5 / 1000),
    }


# ── extended analytics ───────────────────────────────────────────────────────

STOPWORDS = {
    "a","an","the","and","or","but","in","on","at","to","for","of","with",
    "is","was","are","be","been","by","it","its","from","this","that","these",
    "those","i","my","your","we","you","he","she","they","our","their","not",
    "do","did","get","got","how","why","what","when","who","will","can","new",
    "up","out","all","have","has","more","which","also","into","so","than",
    "just","now","make","if","as","about","vs","ft","ep"
}


def duration_to_seconds(iso: str) -> int:
    """Convert ISO 8601 duration string to total seconds."""
    h = re.search(r"(\d+)H", iso)
    m = re.search(r"(\d+)M", iso)
    s = re.search(r"(\d+)S", iso)
    return ((int(h.group(1)) if h else 0) * 3600 +
            (int(m.group(1)) if m else 0) * 60 +
            (int(s.group(1)) if s else 0))


def analyze_extended(videos: list, channel_avg_views: float) -> dict:
    """Compute extended analytics from fetched video list."""
    if not videos:
        return {
            "day_stats": {}, "views_over_time": [], "keywords": [],
            "length_buckets": {}, "viral_videos": [],
            "shorts_count": 0, "long_count": 0,
            "shorts_avg_views": 0.0, "long_avg_views": 0.0,
            "upload_consistency": None,
            "hour_stats": {str(h): 0 for h in range(24)},
            "avg_seo_score": 0, "seo_scores": [],
        }

    DAY_NAMES = list(calendar.day_abbr)  # Mon..Sun
    day_views  = defaultdict(list)
    hour_views = defaultdict(list)
    keyword_map = defaultdict(lambda: {"count": 0, "total_views": 0})
    length_map  = {k: {"count": 0, "total": 0}
                   for k in ("Shorts", "<5min", "5-15min", "15-30min", "30-60min", "60min+")}
    viral_videos  = []
    views_over_time = []
    shorts_views, long_views = [], []
    seo_scores_raw = []
    dates = []

    for v in videos:
        s   = v.get("snippet", {})
        st  = v.get("statistics", {})
        cd  = v.get("contentDetails", {})

        views    = safe_int(st.get("viewCount"))
        pub      = s.get("publishedAt", "")
        title    = s.get("title", "")
        dur_iso  = cd.get("duration", "PT0S")
        secs     = duration_to_seconds(dur_iso)
        vid_url  = f"https://youtube.com/watch?v={v['id']}"
        thumb    = s.get("thumbnails", {}).get("medium", {}).get("url", "")

        # SEO score
        seo = compute_seo_score(title)
        seo_scores_raw.append({"title": title, "score": seo, "url": vid_url})

        # Day of week + hour
        if pub:
            try:
                dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                day_views[dt.weekday()].append(views)
                hour_views[dt.hour].append(views)
                dates.append(dt)
                views_over_time.append({"date": pub[:10], "views": views, "title": title[:60]})
            except Exception:
                pass

        # Keywords
        for word in re.split(r"[\s\-_|/]+", title):
            word = re.sub(r"[^a-zA-Z0-9']", "", word).lower()
            if len(word) >= 3 and word not in STOPWORDS:
                keyword_map[word]["count"] += 1
                keyword_map[word]["total_views"] += views

        # Length buckets
        if secs > 0:
            if secs <= 60:       bucket = "Shorts"
            elif secs <= 300:    bucket = "<5min"
            elif secs <= 900:    bucket = "5-15min"
            elif secs <= 1800:   bucket = "15-30min"
            elif secs <= 3600:   bucket = "30-60min"
            else:                bucket = "60min+"
            length_map[bucket]["count"] += 1
            length_map[bucket]["total"] += views

            if secs <= 60:
                shorts_views.append(views)
            else:
                long_views.append(views)

        # Viral score
        viral_score = round(views / channel_avg_views, 2) if channel_avg_views else 0
        if viral_score >= 3:
            viral_videos.append({
                "title":       title,
                "url":         vid_url,
                "thumbnail":   thumb,
                "views":       views,
                "views_fmt":   fmt_num(views),
                "viral_score": viral_score,
                "published":   pub[:10],
                "duration":    fmt_duration(dur_iso),
            })

    # Sort views over time chronologically
    views_over_time.sort(key=lambda x: x["date"])

    # Day stats
    day_stats = {}
    for i, name in enumerate(DAY_NAMES):
        v_list = day_views.get(i, [])
        day_stats[name] = int(sum(v_list) / len(v_list)) if v_list else 0

    # Top 15 keywords by avg views
    keywords = sorted(
        [{"word": w, "count": d["count"],
          "avg_views": int(d["total_views"] / d["count"]) if d["count"] else 0,
          "total_views": d["total_views"]}
         for w, d in keyword_map.items()],
        key=lambda x: x["avg_views"], reverse=True
    )[:15]

    # Length buckets
    length_buckets = {
        k: {"count": v["count"], "avg_views": int(v["total"] / v["count"]) if v["count"] else 0}
        for k, v in length_map.items()
    }

    # Upload consistency (std dev of gaps in days)
    upload_consistency = None
    if len(dates) >= 3:
        dates_sorted = sorted(dates, reverse=True)
        gaps = [(dates_sorted[i] - dates_sorted[i+1]).days
                for i in range(len(dates_sorted) - 1)]
        try:
            upload_consistency = round(statistics.stdev(gaps), 1)
        except statistics.StatisticsError:
            pass

    # Hour stats: average views per upload hour
    hour_stats = {}
    for h in range(24):
        v_list = hour_views.get(h, [])
        hour_stats[str(h)] = int(sum(v_list) / len(v_list)) if v_list else 0

    # SEO scores
    seo_scores_sorted = sorted(seo_scores_raw, key=lambda x: x["score"], reverse=True)
    avg_seo_score = int(sum(x["score"] for x in seo_scores_raw) / len(seo_scores_raw)) if seo_scores_raw else 0

    return {
        "day_stats":          day_stats,
        "views_over_time":    views_over_time[-100:],  # cap at 100 points
        "keywords":           keywords,
        "length_buckets":     length_buckets,
        "viral_videos":       sorted(viral_videos, key=lambda x: x["viral_score"], reverse=True)[:20],
        "shorts_count":       len(shorts_views),
        "long_count":         len(long_views),
        "shorts_avg_views":   int(sum(shorts_views) / len(shorts_views)) if shorts_views else 0,
        "long_avg_views":     int(sum(long_views) / len(long_views)) if long_views else 0,
        "upload_consistency": upload_consistency,
        "hour_stats":         hour_stats,
        "avg_seo_score":      avg_seo_score,
        "seo_scores":         seo_scores_sorted[:10],
    }


# ── display ──────────────────────────────────────────────────────────────────

def display(data: dict):
    console.print()

    # Header panel
    header = Text()
    header.append(f"  {data['channel_name']}\n", style="bold yellow")
    header.append(f"  Created: {data['created_at']}   Country: {data['country']}\n", style="dim")
    if data["description"]:
        header.append(f"\n  {data['description']}", style="italic dim")
    console.print(Panel(header, box=box.DOUBLE_EDGE, border_style="yellow"))

    # Key stats row
    stat_panels = [
        Panel(f"[bold cyan]{fmt_num(data['subscribers'])}[/]\nSubscribers", box=box.ROUNDED),
        Panel(f"[bold green]{fmt_num(data['total_views'])}[/]\nTotal Views", box=box.ROUNDED),
        Panel(f"[bold magenta]{data['video_count']:,}[/]\nVideos", box=box.ROUNDED),
        Panel(f"[bold blue]{fmt_num(data['avg_views'])}[/]\nAvg Views/Video", box=box.ROUNDED),
        Panel(f"[bold red]{data['eng_rate']:.2f}%[/]\nEngagement Rate", box=box.ROUNDED),
    ]
    if data["avg_days_between"] is not None:
        freq = data["avg_days_between"]
        freq_str = f"every {freq:.1f}d" if freq >= 1 else f"every {freq*24:.1f}h"
        stat_panels.append(Panel(f"[bold white]{freq_str}[/]\nUpload Freq.", box=box.ROUNDED))

    console.print(Columns(stat_panels, equal=True, expand=True))

    # Top 10 videos
    console.print("\n[bold yellow]Top 10 Videos by Views[/]")
    tbl = Table(box=box.SIMPLE_HEAD, show_lines=False, expand=True)
    tbl.add_column("#",        style="dim",         width=3,  no_wrap=True)
    tbl.add_column("Title",    style="white",        ratio=4)
    tbl.add_column("Published",style="dim",          width=11, no_wrap=True)
    tbl.add_column("Duration", style="cyan",         width=9,  no_wrap=True)
    tbl.add_column("Views",    style="green",        width=9,  no_wrap=True, justify="right")
    tbl.add_column("Likes",    style="magenta",      width=9,  no_wrap=True, justify="right")
    tbl.add_column("Comments", style="blue",         width=9,  no_wrap=True, justify="right")

    for i, v in enumerate(data["top_videos"], 1):
        s    = v["snippet"]
        st   = v["statistics"]
        cd   = v["contentDetails"]
        tbl.add_row(
            str(i),
            s.get("title", "")[:80],
            s.get("publishedAt", "")[:10],
            fmt_duration(cd.get("duration", "PT0S")),
            fmt_num(safe_int(st.get("viewCount"))),
            fmt_num(safe_int(st.get("likeCount"))),
            fmt_num(safe_int(st.get("commentCount"))),
        )
    console.print(tbl)

    # Monthly uploads (last 12 months)
    if data["monthly"]:
        console.print("[bold yellow]Monthly Upload Activity (last 12 months)[/]")
        mtbl = Table(box=box.SIMPLE_HEAD, show_lines=False)
        mtbl.add_column("Month", style="dim", width=9)
        mtbl.add_column("Videos", style="cyan", width=8, justify="right")
        mtbl.add_column("Bar", style="green")
        max_count = max(data["monthly"].values()) or 1
        for month, count in data["monthly"].items():
            bar = "█" * int(count / max_count * 30)
            mtbl.add_row(month, str(count), bar)
        console.print(mtbl)

    # Views distribution
    vl = sorted(data["views_list"], reverse=True)
    if vl:
        buckets = {"1B+": 0, "100M+": 0, "10M+": 0, "1M+": 0,
                   "100K+": 0, "10K+": 0, "<10K": 0}
        for v in vl:
            if v >= 1_000_000_000: buckets["1B+"] += 1
            elif v >= 100_000_000: buckets["100M+"] += 1
            elif v >= 10_000_000:  buckets["10M+"] += 1
            elif v >= 1_000_000:   buckets["1M+"] += 1
            elif v >= 100_000:     buckets["100K+"] += 1
            elif v >= 10_000:      buckets["10K+"] += 1
            else:                  buckets["<10K"] += 1

        console.print("[bold yellow]Views Distribution[/]")
        dtbl = Table(box=box.SIMPLE_HEAD, show_lines=False)
        dtbl.add_column("Range", style="dim", width=8)
        dtbl.add_column("Count", style="cyan", width=7, justify="right")
        dtbl.add_column("Bar", style="blue")
        max_b = max(buckets.values()) or 1
        for label, count in buckets.items():
            bar = "▪" * int(count / max_b * 30)
            dtbl.add_row(label, str(count), bar)
        console.print(dtbl)

    console.print(f"\n[dim]Analysis based on {data['fetched_count']} most recent videos.[/]\n")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        console.print("[yellow]Usage:[/] python analyzer.py <channel_url | @handle | channel_id>")
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    api_key = load_api_key()
    youtube = build("youtube", "v3", developerKey=api_key)

    with console.status(f"[cyan]Resolving channel: {query}[/]"):
        channel = resolve_channel(youtube, query)

    content_details = channel.get("contentDetails", {})
    uploads_id = content_details.get("relatedPlaylists", {}).get("uploads")

    if not uploads_id:
        console.print("[red]Could not find uploads playlist for this channel.[/]")
        sys.exit(1)

    channel_name = channel["snippet"]["title"]
    with console.status(f"[cyan]Fetching videos for {channel_name}...[/]"):
        videos = fetch_videos(youtube, uploads_id, max_videos=200)

    with console.status("[cyan]Analyzing...[/]"):
        data = analyze(channel, videos)

    display(data)


if __name__ == "__main__":
    main()
