from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

class DataAnalysisRequest(BaseModel):
    data_sources: List[str] = Field(..., description="List of data source URLs or IDs")
    analysis_focus: Optional[str] = Field(None, description="Specific focus for analysis")
    requirements: Optional[str] = Field(None, description="Specific requirements or questions")
    output_format: Optional[str] = Field("json", description="Output format (json, csv, pdf)")

class ContentCreationRequest(BaseModel):
    content_type: str = Field(..., description="Type of content to create")
    topic: str = Field(..., description="Main topic or subject")
    target_audience: Optional[str] = Field(None, description="Target audience")
    tone: Optional[str] = Field("professional", description="Content tone")
    length: Optional[str] = Field("medium", description="Content length")
    data_sources: Optional[List[str]] = Field(None, description="Data sources for content")

class ResearchRequest(BaseModel):
    research_topic: str = Field(..., description="Research topic or question")
    research_depth: Optional[str] = Field("medium", description="Research depth (shallow, medium, deep)")
    sources: Optional[List[str]] = Field(None, description="Specific sources to include")
    output_sections: Optional[List[str]] = Field(None, description="Required output sections")