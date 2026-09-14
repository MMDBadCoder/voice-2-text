"""Private process entry point for one supervised recording."""
import sys
import os
from . import config
config.apply_thread_env()
from .tasks import run_transcription

if __name__ == "__main__":
    os.environ["VOICE_SUPERVISED_JOB"] = sys.argv[1]
    run_transcription(sys.argv[1])
