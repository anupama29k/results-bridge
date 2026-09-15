FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# the robotic-assays library (ecosystem hub) is cloned at build time
RUN git clone --depth 1 https://github.com/anupama29k/robotic-assays /opt/robotic-assays
ENV ROBOTIC_ASSAYS_PATH=/opt/robotic-assays
COPY src ./src
COPY criteria.json .
ENV PYTHONPATH=/app/src
EXPOSE 8080
CMD ["uvicorn", "results_bridge.main:app", "--host", "0.0.0.0", "--port", "8080"]