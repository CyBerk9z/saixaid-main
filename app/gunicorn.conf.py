# gunicorn.conf.py
import multiprocessing

# ワーカーの設定
max_requests = 1000  # ワーカーが再起動するまでのリクエスト数
max_requests_jitter = 50  # 再起動のタイミングをランダム化

# ログの設定
log_file = "-"  # 標準出力にログを出力

# バインディング
bind = "0.0.0.0:8000"  # ポート番号を8000に変更

# ワーカークラス
worker_class = "uvicorn.workers.UvicornWorker"
workers = (multiprocessing.cpu_count() * 2) + 1  # CPU数に基づいてワーカー数を設定

# タイムアウト設定
timeout = 120
graceful_timeout = 30

# ログ設定
accesslog = "-"
errorlog = "-"
capture_output = True

# プロキシ設定
forwarded_allow_ips = "*"