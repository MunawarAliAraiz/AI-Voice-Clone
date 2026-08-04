"""
AI Voice Clone Studio — HTTP layer.

Owned by B2 in Wave 2. Routers live here; `schemas/` is frozen in Wave 0.

Two things this package must get right, both of which were live bugs:

  1. MIDDLEWARE ORDER. Starlette runs the LAST-ADDED middleware OUTERMOST. The
     predecessor added CORS first and the API-key check second, so the key check
     ran outside CORS: a preflight OPTIONS carries no X-API-Key, got a 403 with
     no CORS headers, and every cross-origin write broke the moment VCS_API_KEY
     was set. Add the API-key middleware FIRST (innermost) and CORS LAST
     (outermost).

  2. NO DUPLICATE ROUTES. `routers/models.py`'s endpoints were unreachable
     because `routers/settings.py` registered the same paths first and shadowed
     them. Startup asserts on duplicate (method, path) and refuses to boot.
"""
