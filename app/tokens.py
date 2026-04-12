import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
AZURE_CLIENT_ID = os.environ["AZURE_CLIENT_ID"]
AZURE_CLIENT_SECRET = os.environ["AZURE_CLIENT_SECRET"]
REDIRECT_URI = os.environ["REDIRECT_URI"]
AZURE_REFRESH_TOKEN = os.environ["AZURE_REFRESH_TOKEN"]
