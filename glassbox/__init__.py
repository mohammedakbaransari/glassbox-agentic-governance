__version__ = "1.2.0"

# Optional dependency hint: if PySpark is installed, load module so
# environment detection based on sys.modules is reliable in tests/tools.
try:
	import pyspark as _pyspark  # noqa: F401
except ImportError:
	pass
