import os
import base64
import json
import requests
import uuid
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = Flask(__name__)

# Environment variables
API_KEY = os.getenv('API_KEY')
STORAGE_PATH = os.getenv('STORAGE_PATH', '/tmp/')
GCP_SA_CREDENTIALS = os.getenv('GCP_SA_CREDENTIALS')
GDRIVE_USER = os.getenv('GDRIVE_USER')

# Create STORAGE_PATH if it doesn't exist
os.makedirs(STORAGE_PATH, exist_ok=True)

def authenticate(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        if api_key and api_key == API_KEY:
            return f(*args, **kwargs)
        return jsonify(
            {
                'code': 401,
                'message': 'Unauthorized'
            }), 401
    return decorated_function

def generate_unique_filename(original_filename):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    random_string = uuid.uuid4().hex[:6]
    file_extension = os.path.splitext(original_filename)[1]
    return f"{timestamp}_{random_string}{file_extension}"

@app.route('/gdrive-upload', methods=['POST'])
@authenticate
def gdrive_upload():
    data = request.json
    required_params = ['file_url', 'filename', 'folder_id', 'id', 'webhook_url']
    
    if not all(param in data for param in required_params):
        return jsonify(
                {
                    'code': 400,
                    'message': 'Missing required parameters'
                }), 400

    # Respond immediately with 202 status
    response = jsonify(
        {
            'code': 202,
            'message': 'Processing'
        })
    response.status_code = 202

    # Continue processing in the background
    def process_request():
        try:
            # Generate a unique filename for the download
            unique_filename = generate_unique_filename(data['filename'])
            temp_file_path = os.path.join(STORAGE_PATH, unique_filename)

            # Download file
            with requests.get(data['file_url'], stream=True) as r:
                r.raise_for_status()
                with open(temp_file_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)

            # Upload to Google Drive
            credentials = service_account.Credentials.from_service_account_info(
                json.loads(base64.b64decode(GCP_SA_CREDENTIALS)),
                scopes=['https://www.googleapis.com/auth/drive']
            )
            credentials = credentials.with_subject(GDRIVE_USER)
            drive_service = build('drive', 'v3', credentials=credentials)

            file_metadata = {
                'name': data['filename'],  # Use the original filename for Google Drive
                'parents': [data['folder_id']]
            }
            media = MediaFileUpload(temp_file_path, resumable=True)
            file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()

            # Delete the temporary file
            os.remove(temp_file_path)

            # Send webhook
            webhook_data = {
                'endpoint': '/gdrive-upload',
                'id': data['id'],
                'file_id': file.get('id'),
                'code': 200,
                'message': 'Success'
            }
            requests.post(data['webhook_url'], json=webhook_data)

        except Exception as e:
            # Send error webhook
            webhook_data = {
                'endpoint': '/gdrive-upload',
                'id': data['id'],
                'file_id': None,
                'code': 500,
                'message': str(e)
            }
            requests.post(data['webhook_url'], json=webhook_data)

    # Start background processing
    from threading import Thread
    thread = Thread(target=process_request)
    thread.start()

    return response

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)