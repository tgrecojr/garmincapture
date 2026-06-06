"""garmincapture — thin Garmin Connect raw-capture service feeding the bronze layer.

This package authenticates once to Garmin Connect (auth fully delegated to the
``garminconnect`` library), pulls the entire available data surface, and lands it
into the shared bronze layer with no transformation, at two provenance grades:

* ``raw``           — true on-the-wire bytes (the FIT/original activity files).
* ``reserialized``  — the parsed JSON the library returns, deterministically
                      re-serialized via ``json.dumps``.

See the package modules for the implementation of each concern.
"""

__version__ = "0.1.0"
