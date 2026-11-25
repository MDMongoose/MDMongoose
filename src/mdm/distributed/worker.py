"""The functions Spark sends to its workers.

They are pickled by value (only the standard library is referenced here), so a worker can unpickle them before
the mdm package is importable; the first thing they do is make it importable (the source folder, or the zip the
driver added as a Spark artifact), then unpickle the task's payload and call mdm.distributed.tasks.<task>.
"""


def mapper(task: str, src: str, payload: bytes):
    """For DataFrame.mapInPandas: iterator of pandas DataFrames -> iterator of pandas DataFrames."""
    def run(batches):
        import importlib
        import os
        import pickle
        import sys
        if src and os.path.isdir(src) and src not in sys.path:
            sys.path.insert(0, src)
        fn = getattr(importlib.import_module("mdm.distributed.tasks"), task)
        yield from fn(pickle.loads(payload), batches)
    return run


def grouper(task: str, src: str, payload: bytes):
    """For GroupedData.applyInPandas: one group (a pandas DataFrame) -> a pandas DataFrame."""
    def run(pdf):
        import importlib
        import os
        import pickle
        import sys
        if src and os.path.isdir(src) and src not in sys.path:
            sys.path.insert(0, src)
        fn = getattr(importlib.import_module("mdm.distributed.tasks"), task)
        return fn(pickle.loads(payload), pdf)
    return run
