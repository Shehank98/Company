FROM node:22-slim AS web
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
# ffmpeg renders the Shorts; fonts-dejavu-core is the caption font (ffmpeg uses libass for captions)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
COPY pyproject.toml ./
RUN uv pip install --system --no-cache -r pyproject.toml
COPY agentco ./agentco
COPY migrations ./migrations
COPY --from=web /web/dist ./web/dist
# api (default). Worker / scheduler services override the start command, see railway.toml.
CMD ["sh", "-c", "python -m agentco.db && python -m agentco.seed && uvicorn agentco.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
