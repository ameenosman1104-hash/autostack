import sys
import os

# Add parent directory to path so we can import run and app modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app
