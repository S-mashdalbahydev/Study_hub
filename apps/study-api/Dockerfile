FROM python:3.12-slim@sha256:804ddf3251a60bbf9c92e73b7566c40428d54d0e79d3428194edf40da6521286
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# The service never runs as root. /objects is where originals and artifacts live.
RUN groupadd --gid 10001 studyhub \
 && useradd --uid 10001 --gid studyhub --no-create-home --shell /usr/sbin/nologin studyhub \
 && mkdir -p /objects && chown studyhub:studyhub /objects
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
USER studyhub
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
