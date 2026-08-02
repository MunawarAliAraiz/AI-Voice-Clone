import sys
import multiprocessing

# This is required for PyInstaller multiprocessing on Windows
if sys.platform.startswith('win'):
    multiprocessing.freeze_support()

import uvicorn
from app.config import settings
from app.main import app

if __name__ == "__main__":
    # When running via PyInstaller, we pass the app instance directly
    # because string-based imports ("app.main:app") fail in frozen binaries.
    uvicorn.run(app, host=settings.host, port=settings.port)
