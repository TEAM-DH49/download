import os
import asyncio
from flask import Flask, request
from telegram import Update

from bot import create_bot_app

app = Flask(__name__)

bot_app = None

def get_bot_app():
    global bot_app
    if bot_app is None:
        bot_app = create_bot_app()
    return bot_app


@app.route('/webhook', methods=['POST'])
def webhook():
    telegram_app = get_bot_app()
    update = Update.de_json(request.get_json(), telegram_app.bot)
    asyncio.run(telegram_app.process_update(update))
    return 'OK'


@app.route('/health', methods=['GET'])
def health():
    return 'OK'


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3000))
    app.run(host='0.0.0.0', port=port)
