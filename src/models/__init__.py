"""Entity classes: validated, immutable domain objects (docs/backend_design.md)."""
from src.models.base import Entity
from src.models.employee import Employee, Position
from src.models.project import Assignment, Project
from src.models.review import Review

__all__ = ["Entity", "Employee", "Position", "Project", "Assignment", "Review"]
