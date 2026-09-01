"""The only place SQLAlchemy exists. `db.py` converts at the edge, so nothing
above it knows what a session is.

Empty on purpose: importing any submodule runs this first, so whatever lives
here is paid for by every import of the package.
"""
