**Simple Ollama Mattermost Bot**

This is a very simple mattermost bot which interacts with an ollama llm instance (https://ollama.com/). This is useful if you cannot use mattermost co-pilot beause of MySQL as example.
You can interact with the bot by ether mention the bot in a channel where he is or users can send direct messages to the bot.

Usage:

pip install requests mattermostdriver

replace chat url, bot token and teamname, ollama url and ollama model in bot.py and just run python3 bot.py

If you like to disable the context tracking in ollama (to remember the conversations per user longer) set ENABLE_CONTEXT_TRACKING to False

Make sure the bot in mattermost is a member of the correct team
