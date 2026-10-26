import os

bind = f"0.0.0.0:{os.getenv('PORT', '8051')}"
workers = 1
worker_class = 'gthread'
threads = 8
timeout = 120
graceful_timeout = 30
max_requests = 1000
max_requests_jitter = 100
# Query strings include signed media tokens; do not write them to access logs.
accesslog = '-'
access_log_format = '%(m)s %(U)s %(s)s %(L)s'
errorlog = '-'
