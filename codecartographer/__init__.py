"""CodeCartographer — make an unfamiliar codebase legible before you change it.

The analysis engine is pure standard library: no numpy, no scikit-learn, no
networkx. That matters for the systems this tool targets, which often sit on
locked-down machines with no package index access. Flask is needed only for
the interactive dashboard; the CLI and the HTML report work without it.
"""

from .models import AnalysisConfig
from .analysis.engine import Analysis, analyse

__version__ = "2.0.0"
__all__ = ["Analysis", "AnalysisConfig", "analyse", "__version__"]
