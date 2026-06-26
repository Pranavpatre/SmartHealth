#!/bin/bash
set -e

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║              SmartHealth — Starting Demo Stack               ║"
echo "╚══════════════════════════════════════════════════════════════╝"

# Copy .env if it doesn't exist
if [ ! -f infrastructure/docker/.env ]; then
  cp infrastructure/docker/.env.example infrastructure/docker/.env
  echo "✓ Created .env from template"
fi

# Start infrastructure
echo ""
echo "1/4 Starting Docker services..."
docker compose -f infrastructure/docker/docker-compose.yml up -d postgres redis

echo "   Waiting for PostgreSQL to be ready..."
until docker compose -f infrastructure/docker/docker-compose.yml exec -T postgres pg_isready -U smarthealth -d smarthealth > /dev/null 2>&1; do
  sleep 2
done
echo "✓ PostgreSQL ready"

# Run migrations
echo ""
echo "2/4 Running database migrations..."
docker compose -f infrastructure/docker/docker-compose.yml run --rm api \
  sh -c "cd /app && alembic upgrade head"
echo "✓ Migrations applied"

# Seed demo data
echo ""
echo "3/4 Seeding demo data..."
docker compose -f infrastructure/docker/docker-compose.yml run --rm api \
  sh -c "cd /app && psql \$DATABASE_URL < /app/../data/schemas/002_seed_demo.sql 2>/dev/null || true"
docker compose -f infrastructure/docker/docker-compose.yml run --rm api \
  sh -c "pip install psycopg2-binary -q && python /app/../scripts/demo_seed.py"
echo "✓ Demo data seeded"

# Start all services
echo ""
echo "4/4 Starting all services..."
docker compose -f infrastructure/docker/docker-compose.yml up -d
echo "✓ All services started"

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                    SmartHealth is Ready                      ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Dashboard:    http://localhost:3000                         ║"
echo "║  Field App:    http://localhost:3001                         ║"
echo "║  API Docs:     http://localhost:8000/docs                    ║"
echo "║  Grafana:      http://localhost:3002  (admin/smarthealth)    ║"
echo "║  Prometheus:   http://localhost:9090                         ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "Run 'docker compose -f infrastructure/docker/docker-compose.yml logs -f api' to watch logs."
