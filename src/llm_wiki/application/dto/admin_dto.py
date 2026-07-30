from dataclasses import dataclass


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
    description: str | None = None
    job_type: str = "background_task"
    enabled: bool = True
    command: str | None = None
