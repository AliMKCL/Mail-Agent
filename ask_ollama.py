import requests
import json
import os

from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# For testing using llm.
client = OpenAI(api_key=OPENAI_API_KEY)


def slm_response(query: str):

    # Set up the base URL for the local Ollama API
    url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    url = url + "/api/chat"

    payload = {
        "model": "mistral:latest",  # Replace with the model name you're using
        "messages": [{"role": "user", "content": query}]
    }

    #print(f"DEBUG: Sending request to Ollama at {url}")
    #print(f"DEBUG: Payload model: {payload['model']}")
    print(f"DEBUG: Prompt length: {len(query)}")

    try:
        # Send the HTTP POST request with streaming enabled
        response = requests.post(url, json=payload, stream=True, timeout=(5, None)) # Stream: Grab response as it is being typed
        print(f"DEBUG: Ollama response status: {response.status_code}")
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Failed to connect to Ollama: {e}")
        return f"Connection error: {e}"

    #print("Query asked to Ollama:", query)

    # Collect the slm's streamed content into a single string
    collected_parts = []

    # Check the response status
    if response.status_code == 200:
        line_count = 0
        for line in response.iter_lines(decode_unicode=True):   # Streams the response line-by-line
            line_count += 1
            
            if not line: # Skip empty lines
                continue
            try:
                json_data = json.loads(line) # Load the line as JSON
                #print(f"DEBUG: Line {line_count} JSON: {json_data}")
            except json.JSONDecodeError as e:
                #print(f"DEBUG: Line {line_count} JSON decode error: {e}, raw line: '{line}'")
                # skip non-json lines
                continue

            # Ollama streaming lines include the message content under ['message']['content']
            msg = json_data.get("message")
            if msg and isinstance(msg, dict):
                content = msg.get("content")
                if content:
                    collected_parts.append(content)

        # Join all parts into the final text
        final_text = "".join(collected_parts)
        print(f"DEBUG: Final collected text length: {len(final_text)}")
        print(f"DEBUG: Total lines processed: {line_count}")
    else:
        # On error, return the raw response text for inspection
        print(f"ERROR: Ollama returned status {response.status_code}")
        print(f"ERROR: Response text: {response.text}")
        final_text = f"Error {response.status_code}: {response.text}"

    # Ensure the response object is closed
    try:
        response.close()
    except Exception:
        pass

    return final_text


def llm_response(query: str):
    response = client.chat.completions.create(
        model="gpt-5-mini",
        messages=[{"role": "user", "content": query}]
    )

    raw_response= response.choices[0].message.content.strip()

    return raw_response