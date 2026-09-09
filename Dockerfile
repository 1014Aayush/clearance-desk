FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# ffmpeg cuts the short audio samples used for acoustic identification of
# music cues. Without it that stage degrades to "unidentified" and the cue
# sheet is requested instead — the service still runs, just with less to
# hand the research stage.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

# Dependencies first so the layer caches across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY clearance_desk ./clearance_desk

# The bundled sample cuts. Media is not Python, so it sits beside the package
# rather than inside it — but it has to reach the image, or the samples the UI
# offers are buttons that 404.
COPY assets ./assets

# Cloud Run injects PORT; default to 8080 for local `docker run`.
ENV PORT=8080
EXPOSE 8080

CMD exec uvicorn clearance_desk.server:app --host 0.0.0.0 --port ${PORT}
