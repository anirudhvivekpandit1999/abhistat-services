from pymongo import MongoClient
import certifi
from core.config import MONGODB_URI

mongo_client = MongoClient(MONGODB_URI, tlsCAFile=certifi.where())
db = mongo_client['abhitech_stat_tool']
external_users = db['external_users'] 