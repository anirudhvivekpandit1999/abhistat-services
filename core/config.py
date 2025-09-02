import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

TEMP_DIR = Path("./temp_files")
TEMP_DIR.mkdir(exist_ok=True)

MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
SECRET_KEY = os.getenv('JWT_SECRET', 'supersecretkey')
ALGORITHM = 'HS256'
ACCESS_TOKEN_EXPIRE_MINUTES = 60
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET') 