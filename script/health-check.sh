#!/bin/bash

echo "=== Enterprise Dashboard Health Check ==="

# Check Docker services
echo "Checking Docker services..."
docker-compose ps

# Check service endpoints
echo -e "\nChecking service endpoints..."

# Main application
if curl -f -s http://localhost >/dev/null; then
    echo "✓ Main Dashboard: HEALTHY"
else
    echo "✗ Main Dashboard: UNHEALTHY"
fi

# Database
if docker exec postgres pg_isready -U dashboard_user >/dev/null 2>&1; then
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

# CrewAI Service
if curl -f -s http://localhost:8000/health >/dev/null; then
    echo "✓ CrewAI Service: HEALTHY"
else
    echo "✗ CrewAI Service: UNHEALTHY"
fi

# Check disk space
echo -e "\nDisk Space Usage:"
df -h | grep -E "(Filesystem|/dev/)"

# Check memory usage
echo -e "\nMemory Usage:"
free -h

# Check Docker resource usage
echo -e "\nDocker Resource Usage:"
docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}"

echo "=== Health Check Complete ==="