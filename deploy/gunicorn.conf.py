bind = "127.0.0.1:8000"
workers = 3
threads = 2
timeout = 120
accesslog = "/var/log/hosting-panel/gunicorn-access.log"
errorlog = "/var/log/hosting-panel/gunicorn-error.log"
capture_output = True
worker_tmp_dir = "/dev/shm"
