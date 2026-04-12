from discord_app import bot
from app.tokens import BOT_TOKEN
from wait_times_app import WaitTimesApp

bot.add_cog(WaitTimesApp(bot))
bot.run(BOT_TOKEN)
