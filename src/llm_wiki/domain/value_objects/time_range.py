from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class TimeRange:
    start: datetime
    end: Optional[datetime] = None

    def contains(self, dt: datetime) -> bool:
        if self.end is None:
            return dt >= self.start
        return self.start <= dt <= self.end
