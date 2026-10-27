"""Reference data mirrored from sql/02_oltp_reference_data.sql.

The synthesizer must not depend on a running database, so the business rules it obeys are
mirrored here. tests/test_synthesizer.py checks that these values match the database exactly.
"""

DEPARTMENTS = {1: "Human Resources", 2: "Research & Development", 3: "Sales"}
DEPARTMENT_IDS = {name: dept_id for dept_id, name in DEPARTMENTS.items()}
DEPARTMENT_ALIASES = {"Human Resources": "HR", "Research & Development": "R&D", "Sales": "Sales Dept"}

# role -> (min level, max level)   finding F2
JOB_ROLES = {
    "Healthcare Representative": (2, 4),
    "Human Resources": (1, 3),
    "Laboratory Technician": (1, 3),
    "Manager": (3, 5),
    "Manufacturing Director": (2, 4),
    "Research Director": (3, 5),
    "Research Scientist": (1, 3),
    "Sales Executive": (2, 4),
    "Sales Representative": (1, 2),
}

# the 11 valid department-role pairs   finding F1
DEPARTMENT_ROLES = {
    "Human Resources": ["Human Resources", "Manager"],
    "Research & Development": ["Healthcare Representative", "Laboratory Technician", "Manager",
                               "Manufacturing Director", "Research Director", "Research Scientist"],
    "Sales": ["Manager", "Sales Executive", "Sales Representative"],
}

# level -> (title, salary band min, salary band max)   finding F3
JOB_LEVELS = {
    1: ("Entry", 1000, 5000),
    2: ("Intermediate", 2000, 10000),
    3: ("Senior", 5000, 14000),
    4: ("Lead", 11000, 18000),
    5: ("Executive", 18000, 20000),
}

# rating -> (label, hike min %, hike max %)   findings F4, F12
PERFORMANCE_RATINGS = {
    1: ("Low", 0, 4),
    2: ("Good", 5, 10),
    3: ("Excellent", 11, 19),
    4: ("Outstanding", 20, 25),
}

# reason -> is_voluntary
EXIT_REASONS = {
    "Better Opportunity": True,
    "Compensation": True,
    "Work-Life Balance": True,
    "Career Change": True,
    "Relocation": True,
    "Retirement": True,
    "Performance Termination": False,
    "Layoff / Restructuring": False,
    "Contract End": False,
}

EDUCATION_LEVELS = {1: "Below College", 2: "College", 3: "Bachelor", 4: "Master", 5: "Doctor"}
EDUCATION_FIELDS = ["Human Resources", "Life Sciences", "Marketing", "Medical", "Other", "Technical Degree"]

PROJECT_ROLES = ["Project Lead", "Team Member", "Analyst", "Specialist", "Consultant", "Coordinator"]


def roles_valid_at(department: str, level: int) -> list[str]:
    """Roles that exist in a department and allow the given level."""
    return [r for r in DEPARTMENT_ROLES[department] if JOB_ROLES[r][0] <= level <= JOB_ROLES[r][1]]


def band(level: int) -> tuple[int, int]:
    _, low, high = JOB_LEVELS[level]
    return low, high
