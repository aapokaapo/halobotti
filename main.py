from discord_app import bot
from app.tokens import BOT_TOKEN
from database_app.database import create_db_and_tables, engine

create_db_and_tables()
bot.run(BOT_TOKEN)
