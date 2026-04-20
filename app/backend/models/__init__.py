"""Import model modules so Base.metadata is complete at startup."""

import importlib
import pkgutil

for _, module_name, is_pkg in pkgutil.iter_modules(__path__):
    if is_pkg or module_name.startswith("_"):
        continue
    importlib.import_module(f"{__name__}.{module_name}")

from models.auth import OIDCState, User
from models.credit_usage import Credit_usage
from models.messages import Messages
from models.project_files import Project_files
from models.projects import Projects
from models.user_profiles import User_profiles
from models.workspace_runtime_sessions import WorkspaceRuntimeSessions

__all__ = [
    "OIDCState",
    "User",
    "Credit_usage",
    "Messages",
    "Project_files",
    "Projects",
    "User_profiles",
    "WorkspaceRuntimeSessions",
]
