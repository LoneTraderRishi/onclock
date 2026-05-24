from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

# ═══════════════════════════════════════════════════════════════════
# CYBER CAFE MODELS
# ═══════════════════════════════════════════════════════════════════

class CyberSessionStart(BaseModel):
    playstation_id: int
    player_name: str = Field(min_length=1, description="Customer name is required")
    player_phone: str = Field(min_length=1, description="Customer phone is required")
    num_players: int = Field(default=1, gt=0, description="Number of players must be at least 1")
    hours: float = Field(gt=0, description="Hours must be positive")


class CyberAdvanceBooking(BaseModel):
    playstation_id: int
    player_name: str = Field(min_length=1, description="Customer name is required")
    player_phone: str = Field(min_length=1, description="Customer phone is required")
    num_players: int = Field(default=1, gt=0, description="Number of players must be at least 1")
    hours: float = Field(gt=0, description="Hours must be positive")
    scheduled_start: str = Field(description="ISO datetime for the scheduled session start")


class CyberSessionEnd(BaseModel):
    end_reason: Optional[str] = ""


class CyberPlayStationCreate(BaseModel):
    playstation_number: int
    name: str
    hourly_rate: float = Field(default=50, gt=0, description="Hourly rate must be positive")
