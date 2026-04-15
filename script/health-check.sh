#!/bin/bash

echo "=== Prefect Workflow Service Health Check ==="

# Check Docker services
echo "Checking Docker services..."
docker-compose ps

# Check service endpoints
echo -e "\nChecking service endpoints..."

# Prefect Server
if curl -f -s http://localhost:4200/api/health >/dev/null; then
    echo "✓ Prefect Server: HEALTHY"
else
    echo "✗ Prefect Server: UNHEALTHY"
fi

# Database
if docker exec postgres pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then
    echo "✓ PostgreSQL: HEALTHY"
else
    echo "✗ PostgreSQL: UNHEALTHY"
fi

# Redis
if docker exec redis redis-cli --no-auth-warning -a "$REDIS_PASSWORD" ping >/dev/null 2>&1; then
    echo "✓ Redis: HEALTHY"
else
    echo "✗ Redis: UNHEALTHY"
fi

# Check disk space
echo -e "\nDisk Space Usage:"
df -h | grep -E "(Filesystem|/dev/)"

# Check memory usage
echo -e "\nMemory Usage:"
free -h 2>/dev/null || vm_stat 2>/dev/null || echo "Memory info not available"

# Check Docker resource usage
echo -e "\nDocker Resource Usage:"
docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}"

echo "=== Health Check Complete ==="
