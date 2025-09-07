FROM prefecthq/prefect:3.4.16.dev3-python3.12

# Install async PostgreSQL adapter
RUN pip install asyncpg psycopg2-binary

# Expose the port
EXPOSE 4200

# Set the default command
CMD ["prefect", "server", "start", "--host", "0.0.0.0", "--port", "4200"]