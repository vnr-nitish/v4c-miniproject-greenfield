"""Data access layer: one manager per area, each a DatabaseConnection Singleton (docs/backend_design.md)."""
from src.dal.analytics_manager import AnalyticsFilter, AnalyticsManager
from src.dal.employee_manager import EmployeeManager, PositionChange
from src.dal.etl_manager import EtlManager
from src.dal.project_manager import ProjectManager
from src.dal.reference_manager import ReferenceDataManager
from src.dal.review_manager import ReviewManager
from src.dal.schema_manager import SchemaManager, SqlScript

__all__ = ["AnalyticsFilter", "AnalyticsManager", "EmployeeManager", "PositionChange", "EtlManager",
           "ProjectManager", "ReferenceDataManager", "ReviewManager", "SchemaManager", "SqlScript"]
