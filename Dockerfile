# Containerized pipeline: generates the synthetic claims, rebuilds the RCM
# metrics, and runs the invariant suite. CI builds and runs this image.
FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir pytest

COPY . .

CMD ["sh", "-c", "python data_generator/generate_claims_data.py && python engine/build_rcm_metrics.py && pytest tests/ -v"]
