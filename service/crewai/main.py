from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from contextlib import asynccontextmanager
import uvicorn
import os
import logging
from typing import Dict, Any

from crews.data_analysis_crew import DataAnalysisCrew
from crews.content_creation_crew import ContentCreationCrew
from crews.research_crew import ResearchCrew
from models.request_models import *
from models.response_models import *
from utils.database import Database
from utils.redis_client import RedisClient

# Metrics
REQUEST_COUNT = Counter('crewai_requests_total', 'Total requests', ['method', 'endpoint'])
REQUEST_DURATION = Histogram('crewai_request_duration_seconds', 'Request duration')

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global variables for crews
data_analysis_crew = None
content_creation_crew = None
research_crew = None
db = None
redis_client = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize crews and connections on startup"""
    global data_analysis_crew, content_creation_crew, research_crew, db, redis_client
    
    logger.info("Initializing CrewAI Service...")
    
    # Initialize database and Redis
    db = Database()
    redis_client = RedisClient()
    
    # Initialize crews
    data_analysis_crew = DataAnalysisCrew()
    content_creation_crew = ContentCreationCrew()
    research_crew = ResearchCrew()
    
    logger.info("CrewAI Service initialized successfully")
    yield
    
    # Cleanup on shutdown
    logger.info("Shutting down CrewAI Service...")

app = FastAPI(
    title="CrewAI Service",
    description="AI Agent orchestration service for Enterprise Dashboard",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security
security = HTTPBearer()

def verify_api_key(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Verify API key for authentication"""
    expected_key = os.getenv("CREWAI_API_KEY")
    if not expected_key or credentials.credentials != expected_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return credentials.credentials

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "crewai-service",
        "version": "1.0.0"
    }

@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""
    return generate_latest()

@app.post("/crews/data-analysis/run", response_model=CrewRunResponse)
async def run_data_analysis_crew(
    request: DataAnalysisRequest,
    background_tasks: BackgroundTasks,
    api_key: str = Depends(verify_api_key)
):
    """Run data analysis crew"""
    REQUEST_COUNT.labels(method="POST", endpoint="/crews/data-analysis/run").inc()
    
    with REQUEST_DURATION.time():
        try:
            # Start crew execution in background
            task_id = await data_analysis_crew.kickoff_async(request.dict())
            
            # Store task in Redis for tracking
            await redis_client.store_task(task_id, {
                "type": "data_analysis",
                "status": "running",
                "request": request.dict()
            })
            
            return CrewRunResponse(
                task_id=task_id,
                status="started",
                message="Data analysis crew started successfully"
            )
        except Exception as e:
            logger.error(f"Data analysis crew failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

@app.get("/tasks/{task_id}/status", response_model=TaskStatusResponse)
async def get_task_status(task_id: str, api_key: str = Depends(verify_api_key)):
    """Get task status and results"""
    try:
        task_data = await redis_client.get_task(task_id)
        
        if not task_data:
            raise HTTPException(status_code=404, detail="Task not found")
        
        return TaskStatusResponse(
            task_id=task_id,
            status=task_data.get("status", "unknown"),
            result=task_data.get("result"),
            error=task_data.get("error"),
            created_at=task_data.get("created_at"),
            completed_at=task_data.get("completed_at")
        )
    except Exception as e:
        logger.error(f"Failed to get task status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info"
    )