# ---- Detection service image (Python + Flask + gunicorn) ----
FROM python:3.12-slim
WORKDIR /app
# install dependencies first so Docker can cache this layer when only code changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY detection ./detection
EXPOSE 5001
# ONE worker process: a one-million-row analysis needs ~2-3 GB of RAM, so more workers would multiply that
CMD ["gunicorn", "-b", "0.0.0.0:5001", "-w", "1", "--threads", "2", "--timeout", "900", "detection.app:create_app()"]
