import sys
import os

# Ensure the backend directory is on sys.path so that local packages
# (routers, services, models, core, etc.) can be imported without
# needing PYTHONPATH set explicitly in the environment.
_backend_dir = os.path.dirname(__file__)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
