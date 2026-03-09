#!/usr/bin/env python3
"""Entry point for Justice Praskac API."""
import os

from justice.app import app

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
