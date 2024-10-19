import time
import requests
import threading
from queue import Queue
from datetime import datetime, timedelta
from mattermostdriver import Driver

# Mattermost settings
MATTERMOST_URL = "chat.mattermost.com"  # Replace with your Mattermost URL
BOT_TOKEN = "YOURTOKEN_OF_THE_BOT"  # Replace with your bot token
TEAM_NAME = "MATTERMOST_TEAMNAME"  # Replace with your team name (slug)

# Ollama settings
OLLAMA_API_URL = "http://localhost:11434/api/generate"  # Correct Ollama API endpoint
OLLAMA_MODEL = "mistral"  # Specify the model you want to use

# Enable or disable context tracking
ENABLE_CONTEXT_TRACKING = True  # Set to True to enable context tracking

# Initialize Mattermost driver with the provided settings (disable debug mode)
driver = Driver({
    "url": MATTERMOST_URL,
    "token": BOT_TOKEN,
    "scheme": "https",
    "port": 443,
    "debug": False  # Disable debug mode to reduce shell output
})

# Debug messages flag
debug_messages = True  # Set to False if you want to suppress debug output

# Dictionary to store the timestamp of the last processed message for each channel
last_processed_timestamp = {}
# Dictionary to track the last polling time for each channel
last_poll_time = {}

# Message queue to hold new messages for processing
message_queue = Queue()

# User context dictionary to store conversation context for each user
# Structure: { user_id: (context, last_interaction_time) }
user_context = {}  # Maps user_id to (context, last_interaction_time)

# Store the bot's boot time to avoid responding to messages before it started
boot_time = int(datetime.now().timestamp() * 1000)  # Boot time in milliseconds

# Context expiration duration (4 minutes)
CONTEXT_EXPIRATION_DURATION = timedelta(minutes=4)

# Function to post a message to a Mattermost channel
def post_message_to_mattermost(channel_id, message):
    driver.posts.create_post({
        "channel_id": channel_id,
        "message": message
    })

# Function to send a prompt to the Ollama API and get the response
def get_ollama_response(prompt, context=None):
    headers = {
        "Content-Type": "application/json"
    }
    # Construct the request data
    data = {
        "model": OLLAMA_MODEL,  # Use the dynamic model variable
        "prompt": prompt,
        "stream": False  # Ensure streaming is disabled
    }

    # Only send context if ENABLE_CONTEXT_TRACKING is True and context is provided
    if ENABLE_CONTEXT_TRACKING and context:
        data["context"] = context

    # Debug output: Show what is being sent to Ollama API
    if debug_messages:
        print(f"[DEBUG] Sending to Ollama: {data}")

    try:
        response = requests.post(OLLAMA_API_URL, json=data, headers=headers)
        if response.status_code == 200:
            # Parse the response from Ollama
            ollama_response = response.json()
            return ollama_response.get("response", "No response from Ollama"), ollama_response.get("context", "")
        else:
            print(f"Error: Ollama returned status code {response.status_code}")
            if debug_messages:
                print(f"[DEBUG] Response Content: {response.text}")
            return f"Error: Ollama returned status code {response.status_code}", ""
    except Exception as e:
        print(f"Error communicating with Ollama: {e}")
        return "Error: Unable to get a response from Ollama.", ""

# Worker function to process messages from the queue
def message_processor():
    while True:
        try:
            # Wait for a new message to be added to the queue
            channel_id, message_text, sender_username, message_time = message_queue.get()

            # Update user context and check for expiration
            user_id = sender_username  # Assuming sender_username is the user's ID
            current_time = datetime.now()

            # Initialize or update context for the user
            if user_id not in user_context:
                user_context[user_id] = ("", current_time)  # Start with empty context for the first interaction

            # Expire context if necessary
            context, last_interaction_time = user_context[user_id]
            if ENABLE_CONTEXT_TRACKING and current_time - last_interaction_time > CONTEXT_EXPIRATION_DURATION:
                context = ""  # Reset context if expired
                if debug_messages:
                    print(f"[DEBUG] User context for {user_id} expired.")

            # Only include context if context tracking is enabled
            if ENABLE_CONTEXT_TRACKING and context:
                bot_response, new_context = get_ollama_response(message_text, context)
            else:
                bot_response, new_context = get_ollama_response(message_text)

            if debug_messages:
                print(f"Ollama response: {bot_response}")

            # If context tracking is enabled, update the user's context
            if ENABLE_CONTEXT_TRACKING:
                user_context[user_id] = (new_context, current_time)

            # Post the response back to the channel
            post_message_to_mattermost(channel_id, bot_response)
            if debug_messages:
                print(f"Bot response sent to {sender_username}")

            # Update the last processed timestamp for this channel **after processing**
            last_processed_timestamp[channel_id] = message_time
            if debug_messages:
                print(f"[DEBUG] Updated last_processed_timestamp for channel {channel_id} to {message_time} after processing.")

            # Mark the message as processed
            message_queue.task_done()

        except Exception as e:
            print(f"Error processing message: {e}")

# Function to get the team by name
def get_team_by_name(team_name):
    team = driver.teams.get_team_by_name(team_name)
    return team

# Function to check if the bot was mentioned in the message
def bot_is_mentioned(message, bot_username):
    # Check if the bot's username is mentioned in the message
    return f"@{bot_username}" in message

# Thread function to continuously pull new messages and add to the queue
def message_poller():
    driver.login()  # Log in with the bot token
    print("Bot successfully logged in to Mattermost")

    bot_user_id = driver.users.get_user("me")["id"]
    bot_username = driver.users.get_user(bot_user_id)["username"]
    print(f"Bot user ID: {bot_user_id}, Bot username: {bot_username}")

    # Get the team by name (slug)
    team = get_team_by_name(TEAM_NAME)
    if not team:
        print(f"Error: Team '{TEAM_NAME}' not found!")
        return

    print(f"Bot is now monitoring messages in the team '{team['display_name']}'...")

    while True:
        try:
            # Fetch channels the bot is part of within the specified team
            channels = driver.channels.get_channels_for_user(team_id=team["id"], user_id=bot_user_id)

            for channel in channels:
                # Initialize last_poll_time for the channel if it doesn't exist
                if channel["id"] not in last_poll_time:
                    last_poll_time[channel["id"]] = boot_time

                # Fetch posts since the last poll time by passing 'since' as a query parameter
                since_time = last_poll_time[channel["id"]]
                posts = driver.posts.get_posts_for_channel(channel["id"], params={"since": since_time})

                # Process the posts in reverse order (latest first)
                for post_id in reversed(posts["order"]):
                    post = posts["posts"][post_id]
                    # Check if the message was not sent by the bot itself
                    if post["user_id"] != bot_user_id:
                        message_text = post["message"]
                        sender_user_id = post["user_id"]
                        message_time = float(post["create_at"])

                        # Only process messages created after the bot started
                        if message_time < boot_time:
                            if debug_messages:
                                print(f"[DEBUG] Skipping old message in channel {channel['display_name']}: {message_text}")
                            continue  # Skip old messages

                        # Ensure we're only responding to new messages
                        if channel["id"] in last_processed_timestamp and last_processed_timestamp[channel["id"]] >= message_time:
                            if debug_messages:
                                print(f"[DEBUG] Skipping already processed message in {channel['display_name']}: {message_text}")
                            continue  # Skip already processed messages

                        # Get details about the user who sent the message
                        sender_user = driver.users.get_user(sender_user_id)
                        sender_username = sender_user["username"]

                        # Check if the bot was mentioned in the message (for non-DM channels)
                        if channel["type"] != "D" and not bot_is_mentioned(message_text, bot_username):
                            if debug_messages:
                                print(f"[DEBUG] Bot not mentioned in {channel['display_name']} by {sender_username}, skipping message.")
                            continue  # Ignore messages that don't mention the bot in public channels

                        # Log when a new message is received and bot is mentioned
                        print(f"Received new message in {channel['display_name'] or 'DM'} from {sender_username}: {message_text}")

                        # Add the message to the queue for processing, including the message_time
                        message_queue.put((channel["id"], message_text, sender_username, message_time))
                        if debug_messages:
                            print(f"[DEBUG] Message queued for processing from {sender_username} in {channel['display_name']}.")

                # Update the last polling time for this channel after processing messages
                last_poll_time[channel["id"]] = int(datetime.now().timestamp() * 1000)  # Current time in milliseconds

        except Exception as e:
            print(f"Error while polling messages: {e}")

        # Poll every half second
        time.sleep(1)

if __name__ == "__main__":
    # Start the message poller thread
    poller_thread = threading.Thread(target=message_poller, daemon=True)
    poller_thread.start()

    # Start the message processor thread to handle messages from the queue
    processor_thread = threading.Thread(target=message_processor, daemon=True)
    processor_thread.start()

    # Keep the main thread alive to prevent program exit
    while True:
        time.sleep(1)
