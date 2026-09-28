"""Pydantic request/response schemas for the projects module."""

from pydantic import BaseModel, Field
from typing import List, Optional


class ProjectCreate(BaseModel):
    """Request body for creating a project."""
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)


class ProjectUpdate(BaseModel):
    """Request body for updating a project."""
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)


class ProjectResponse(BaseModel):
    """Single project response."""
    id: int
    slug: str
    title: str
    description: str
    created_at: str
    updated_at: str
    document_count: int = 0
    visibility: str = "private"
    read_only: bool = False


class ProjectDetailResponse(ProjectResponse):
    """Project detail with documents."""
    documents: List[dict] = []
