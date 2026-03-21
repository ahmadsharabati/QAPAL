"""
QAPAL Repair Engine

Pipeline: parse failure → graph lookup → rank candidates → patch → validate
"""

from engine.repair.failure_parser import FailureParser, ParsedFailure, FailureType
from engine.repair.locator_matcher import LocatorMatcher, LocatorMatch
from engine.repair.patch_generator import PatchGenerator, PatchResult
from engine.repair.validator import PatchValidator, PatchValidationResult, PatchStatus
from engine.repair.repair_pipeline import RepairPipeline, RepairResult

__all__ = [
    'FailureParser', 'ParsedFailure', 'FailureType',
    'LocatorMatcher', 'LocatorMatch',
    'PatchGenerator', 'PatchResult',
    'PatchValidator', 'PatchValidationResult', 'PatchStatus',
    'RepairPipeline', 'RepairResult',
]
