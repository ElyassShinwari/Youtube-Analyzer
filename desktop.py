"""
YouTube Channel Analyzer — Desktop launcher
Opens the app in a native window (no browser needed).
"""
import threading
import webview
from app import app

PORT = 5731

def start_flask():
    app.run(port=PORT, use_reloader=False)

if __name__ == "__main__":
    t = threading.Thread(target=start_flask, daemon=True)
    t.start()

    # Start background scheduler (daily/weekly alert jobs)
    import time
    time.sleep(1)  # give Flask a moment to start
    try:
        import scheduler
        scheduler.start()
    except Exception as e:
        print(f"Scheduler warning: {e}")

    webview.create_window(
        title="YouTube Channel Analyzer",
        url=f"http://localhost:{PORT}",
        width=1600,
        height=960,
        min_size=(900, 650),
        background_color="#080808",
        frameless=False,
        easy_drag=False,
    )
    webview.start()
