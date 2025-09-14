FROM prefecthq/prefect:3.4.16.dev3-python3.12

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy workflow files
COPY flows/ ./flows/
COPY tasks/ ./tasks/
COPY blocks/ ./blocks/
COPY *.py ./


EXPOSE 4200

# Set environment variables
ENV PYTHONPATH=/app

# Set the default command using environment variable
CMD sh -c "prefect server start --host 0.0.0.0 --port ${PREFECT_SERVER_API_PORT}"