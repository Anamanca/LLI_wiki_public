from dataclasses import dataclass
from typing import Optional


@dataclass
class SourceInput:
    name: str
    platform: str = "youtube"
    external_id: str = ""
    url: str = ""


@dataclass
class CronJobInput:
    job_id: str
    name: str
    schedule: str
    description: Optional[str] = None
    job_type: str = "background_task"
    enabled: bool = True
    command: Optional[str] = None
