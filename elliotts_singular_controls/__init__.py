"""
Elliott's Singular Controls - Tools and controls for Singular.live

A helper UI and HTTP API for Singular.live with optional TfL data integration.
"""

__version__ = "1.0.14"
__author__ = "BlueElliott"
__license__ = "MIT"

from elliotts_singular_controls.core import app, effective_port

__all__ = ["app", "effective_port", "__version__"]