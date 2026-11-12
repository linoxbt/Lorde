"""Lorde SDK: judge training data with CuratorKIT's quality gates through the Lorde Gateway."""

from .client import AsyncLorde, Lorde
from .errors import LordeAPIError
from .jobs import Job
from .models import BatchResult, GroundingResult, RewardResult

__version__ = "0.1.0"
API_MAJOR = 1
__all__ = ["AsyncLorde", "BatchResult", "GroundingResult", "Job", "Lorde", "LordeAPIError", "RewardResult"]
