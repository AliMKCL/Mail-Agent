"""
data_recorder.py - Simple SLM Response Recorder

This script records only the SLM response JSON arrays to data.json file.
Appends new responses without deleting old ones.
"""

import json
import os
from typing import Any

def record_slm_response(slm_response: Any):
    """
    Record SLM response to data.json file
    
    Args:
        slm_response: The JSON response from the SLM
    """
    data_file = "data.json"
    
    # Load existing data or create empty list
    if os.path.exists(data_file):
        try:
            with open(data_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if not isinstance(data, list):
                    data = []
        except (json.JSONDecodeError, FileNotFoundError):
            data = []
    else:
        data = []
    
    # Append new response
    data.append(slm_response)
    
    # Save back to file
    with open(data_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)

def record_email_processing(*args, **kwargs):
    """Dummy function for compatibility with existing app.py calls"""
    pass

if __name__ == "__main__":
    # Example usage
    sample_response = [
        {"date": "15-12-2023", "description": "Meeting at 2 PM conference room A"},
        {"date": "15-12-2023", "description": "Project deadline - submit materials"}
    ]
    
    record_slm_response(sample_response)
    print("Sample SLM response recorded to data.json")
