"""
pipeline/timing.py
==================
@timed(name): accumulate the wall-clock seconds a stage takes in
state.stage_seconds[name] (written to pipeline_result.json), so runs show where
the time goes per boundary (issue #1).
"""

import functools
import time


def timed(name: str):
    def decorate(stage):
        @functools.wraps(stage)
        async def wrapper(state, *args, **kwargs):
            start = time.monotonic()
            try:
                return await stage(state, *args, **kwargs)
            finally:
                elapsed = time.monotonic() - start
                state.stage_seconds[name] = round(state.stage_seconds.get(name, 0.0) + elapsed, 1)
        return wrapper
    return decorate
