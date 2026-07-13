FROM python:3.14-slim

WORKDIR /app

# Copy the full source before install — setuptools builds the main/api
# modules and the agent/app/integrations packages from these files; with
# only pyproject.toml present the install produces an empty package.
COPY . .

RUN pip install --no-cache-dir .

# Non-root runtime user, created before the model pre-bake so the model
# cache lands in this user's home — the exact path the server reads at
# runtime (~/.cache/vectr/models).
RUN useradd -m appuser && chown -R appuser /app
USER appuser

# Pre-bake the embedder + reranker at build time so the server starts
# offline in seconds. Without this, the first boot downloads both models
# and a hosted health check times out before the server accepts requests.
RUN python -c "from pathlib import Path; from sentence_transformers import SentenceTransformer; SentenceTransformer('ibm-granite/granite-embedding-english-r2', cache_folder=str(Path.home()/'.cache'/'vectr'/'models'), trust_remote_code=True, device='cpu')" \
 && python -c "from pathlib import Path; from sentence_transformers import CrossEncoder; CrossEncoder('BAAI/bge-reranker-base', cache_folder=str(Path.home()/'.cache'/'vectr'/'models'))"

ENV VECTR_WORKSPACE=/app
ENV VECTR_PORT=8765
# Hosted/registry deployments start with an empty note store but must still
# advertise the complete tool surface.
ENV VECTR_MCP_ALL_TOOLS=1

EXPOSE 8765

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8765"]
