"""
Singular Tweaks - Tools and tweaks for controlling Singular.live

A helper UI and HTTP API for Singular.live with optional TfL data integration.
"""

__version__ = "1.0.1"
__author__ = "BlueElliott"
__license__ = "MIT"

from singular_tweaks.core import app, effective_port

__all__ = ["app", "effective_port", "__version__"]